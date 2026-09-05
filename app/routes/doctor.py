import mimetypes
import os

from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, abort, current_app
from flask_login import login_required, current_user
from app import db
from app.models import (
    Doctor, Patient, Appointment, MedicalRecord, Diagnosis, Prescription,
    PrescriptionItem, Medication, LabOrder, LabTestCatalog, LabResult,
    RadiologyOrder, ImagingType, Specialty, Referral, VitalSign,
    Notification, User, Bill, Department, ClinicianTemplate, PatientDocument,
)
from app.routes.decorators import roles_required, permissions_required, log_activity, log_change
from app.access import patient_access_required, require_patient_access, accessible_patient_ids
from app.utils import utcnow, is_clinical_locked
from app.services.timeline import record_event
from app.services import alerts as alert_svc
from app.services.patient_safety import patient_safety_context
from app.services.templates import (normalize_sections, format_structured_notes,
                                    template_allowed_for)
from datetime import datetime

doctor_bp = Blueprint('doctor', __name__)


def _current_doctor():
    return Doctor.query.filter_by(user_id=current_user.id).first()


def flag_prescription_safety(rx):
    """Screen a freshly-written prescription for allergy conflicts and
    drug-drug interactions, creating OPEN clinical alerts for review teams.

    Runs within the caller's transaction (no commit). Duplicate alerts are
    avoided by ``ensure_open_alert`` for the same patient+type+source.
    """
    from app.models import Allergy, DrugInteraction
    from sqlalchemy import or_, and_

    items = [it for it in rx.items if it.medication]
    if not items:
        return

    # Allergy conflicts (structured records + legacy free-text field).
    patient_allergies = set()
    for a in Allergy.query.filter_by(patient_id=rx.patient_id, status='Active').all():
        patient_allergies.add(a.substance.strip().lower())
    if rx.patient.allergies:
        for seg in rx.patient.allergies.split(','):
            if seg.strip():
                patient_allergies.add(seg.strip().lower())
    for it in items:
        med = it.medication
        names = [n for n in ((med.generic_name or '').lower(),
                             (med.brand_name or '').lower()) if n]
        hit = next((al for al in patient_allergies
                    if any(al == n or (len(al) >= 4 and (al in n or n in al))
                           for n in names)), None)
        if hit:
            alert_svc.ensure_open_alert(
                rx.patient_id, 'ALLERGY', severity='CRITICAL',
                title=f'Allergy conflict: {med.generic_name}',
                message=f'Prescription #{rx.id} conflicts with documented allergy "{hit}".',
                source_type='prescription', source_id=rx.id)

    # Drug-drug interactions among the items on this prescription.
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i].medication, items[j].medication
            inter = (DrugInteraction.query
                     .filter(or_(
                         and_(DrugInteraction.medication_a_id == a.id,
                              DrugInteraction.medication_b_id == b.id),
                         and_(DrugInteraction.medication_a_id == b.id,
                              DrugInteraction.medication_b_id == a.id)))
                     .first())
            if inter:
                alert_svc.ensure_open_alert(
                    rx.patient_id, 'DRUG_INTERACTION',
                    severity=(inter.severity or 'MODERATE').upper(),
                    title=f'Interaction: {inter.label}',
                    message=f'Prescription #{rx.id} ({inter.description or ""})',
                    source_type='prescription', source_id=rx.id)


@doctor_bp.route('/dashboard')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def dashboard():
    doctor = _current_doctor()
    if not doctor:
        flash('Doctor profile not found.', 'danger')
        return redirect(url_for('main.home'))
    today = utcnow().date()
    todays_appts = [a for a in doctor.appointments
                    if a.scheduled_at and a.scheduled_at.date() == today]
    pending_labs = LabOrder.query.filter_by(status='Pending', doctor_id=doctor.id).count()
    pending_radio = RadiologyOrder.query.filter_by(status='Pending', doctor_id=doctor.id).count()
    # At-risk patient KPI computed from the doctor's own access scope with SQL
    # only (previously looped the heuristic AI risk model over the whole
    # database — N+1 across the entire patient table). A patient counts when
    # they carry a chronic disease or are elderly AND have a documented
    # abnormal lab result.
    allowed = accessible_patient_ids(current_user)
    ids = allowed or set()
    critical_count = 0
    if ids:
        abnormal_pids = set(pid for (pid,) in db.session.query(LabOrder.patient_id)
                            .join(LabResult, LabOrder.id == LabResult.order_id)
                            .filter(LabOrder.patient_id.in_(ids),
                                    LabResult.is_abnormal.is_(True)).distinct().all())
        for p in Patient.query.filter(Patient.id.in_(ids)).all():
            elderly = (p.date_of_birth and
                       (today - p.date_of_birth).days // 365 >= 65)
            if (p.chronic_diseases or elderly) and p.id in abnormal_pids:
                critical_count += 1

    # --- Enriched context for the premium workspace ---
    from app.models import Task, ClinicalAlert, PatientDocument, Notification as NotifModel
    my_tasks = Task.query.filter(Task.assigned_to == current_user.id) \
        .filter(Task.status.in_(['NEW', 'ASSIGNED', 'IN_PROGRESS'])) \
        .order_by(Task.due_at.asc()).limit(8).all()
    my_alerts = ClinicalAlert.query.filter(
        ClinicalAlert.status == 'OPEN'
    ).order_by(ClinicalAlert.created_at.desc()).limit(8).all()
    # Recently-attended patients (from completed appointments)
    recent_patients = []
    recent_appts = sorted(doctor.appointments,
                          key=lambda a: a.scheduled_at or datetime.min,
                          reverse=True)
    seen_pids = set()
    for a in recent_appts:
        if a.patient and a.patient.id not in seen_pids:
            seen_pids.add(a.patient.id)
            recent_patients.append(a.patient)
        if len(recent_patients) >= 5:
            break

    # --- My Patients (all patients with documented need-to-know) ---
    my_patients = []
    if ids:
        my_patients = (Patient.query.filter(Patient.id.in_(sorted(ids)))
                       .order_by(Patient.id.desc()).limit(80).all())

    # --- Waiting room: checked-in / in-consultation patients today ---
    waiting = [a for a in doctor.appointments
               if a.scheduled_at and a.scheduled_at.date() == today
               and a.status in ('CheckedIn', 'InConsultation')]

    # --- Notifications for the doctor ---
    doctor_notifications = NotifModel.query.filter_by(user_id=current_user.id) \
        .order_by(NotifModel.created_at.desc()).limit(8).all()

    # --- Department (doctor's own portal, e.g. Dermatology) ---
    dept_id = getattr(current_user, 'department_id', None)
    department_name = None
    if dept_id:
        dept = db.session.get(Department, dept_id)
        department_name = dept.name if dept else None
    elif doctor and doctor.specialty:
        dept = Department.query.filter(
            Department.name.ilike(f'%{doctor.specialty.name}%')).first()
        if dept:
            dept_id = dept.id
            department_name = dept.name

    # --- Department attachments: patient uploads routed to the doctor's
    #     department (e.g. Dermatology) so specialists see them immediately. ---
    department_attachments = []
    if dept_id:
        department_attachments = (
            PatientDocument.query.join(Patient, Patient.id == PatientDocument.patient_id)
            .filter(Patient.department_id == dept_id)
            .order_by(PatientDocument.uploaded_at.desc())
            .limit(30).all())
    # Fallback: also surface attachments explicitly tagged as clinical images
    # for any of the doctor's own patients (need-to-know).
    if not department_attachments and ids:
        department_attachments = (
            PatientDocument.query.filter(
                PatientDocument.patient_id.in_(sorted(ids)),
                PatientDocument.document_type == 'clinical_image')
            .order_by(PatientDocument.uploaded_at.desc()).limit(30).all())

    # --- AI Specialists relevant to the doctor's specialty/profile ---
    ai_tools = []
    if doctor and doctor.specialty:
        spec = doctor.specialty.name.lower()
    else:
        spec = ''
    labels_ar_sk = 'آفات الجلد'
    labels_ar_fr = 'كشف الكسور'
    if any(k in spec for k in ('dermat', 'جلد', 'skin')):
        ai_tools.append({'name': 'Skin Lesion AI', 'url': url_for('ai.skin_lesion_detection'),
                         'icon': 'fas fa-person-circle-question', 'ar': labels_ar_sk})
    if any(k in spec for k in ('orthop', 'radi', 'أشعة', 'كسور')):
        ai_tools.append({'name': 'Fracture Detection AI', 'url': url_for('ai.fracture_detection'),
                         'icon': 'fas fa-bone', 'ar': labels_ar_fr})
    if any(k in spec for k in ('dent', 'أسنان')):
        ai_tools.append({'name': 'Tooth Segmentation AI', 'url': url_for('ai.tooth_segmentation'),
                         'icon': 'fas fa-tooth', 'ar': 'تقسيم الأسنان'})
    if not ai_tools:
        ai_tools = [
            {'name': 'Skin Lesion AI', 'url': url_for('ai.skin_lesion_detection'),
             'icon': 'fas fa-person-circle-question', 'ar': labels_ar_sk},
            {'name': 'Fracture Detection AI', 'url': url_for('ai.fracture_detection'),
             'icon': 'fas fa-bone', 'ar': labels_ar_fr},
        ]

    # --- Clinical history statistics across the doctor's patients ---
    clinical_history_count = 0
    if ids:
        clinical_history_count = (
            MedicalRecord.query.filter(MedicalRecord.patient_id.in_(sorted(ids))).count())

    return render_template('doctor/dashboard.html', title='Doctor Dashboard',
                           doctor=doctor, todays_appts=todays_appts,
                           pending_labs=pending_labs, pending_radio=pending_radio,
                           critical_count=critical_count,
                           my_tasks=my_tasks, my_alerts=my_alerts,
                           recent_patients=recent_patients, today=today,
                           my_patients=my_patients, waiting=waiting,
                           doctor_notifications=doctor_notifications,
                           department_attachments=department_attachments,
                           department_name=department_name,
                           ai_tools=ai_tools,
                           clinical_history_count=clinical_history_count)


@doctor_bp.route('/attachments')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def patient_attachments():
    """Patient Attachments — image/clinical attachments patients uploaded to
    the doctor's department (e.g. skin lesion photos to Dermatology), ready to
    be opened or analyzed with the department's AI tool."""
    from app.models import PatientDocument, Department as DeptModel
    doctor = _current_doctor()
    user = current_user
    dept_id = getattr(user, 'department_id', None)
    attachments = []

    # Map the doctor's specialty to a department if they have no department
    # set directly (registration may only carry a specialty).
    if not dept_id and doctor and doctor.specialty:
        spec_name = doctor.specialty.name or ''
        dept = DeptModel.query.filter(
            DeptModel.name.ilike(f'%{spec_name}%')).first()
        dept_id = dept.id if dept else None

    if dept_id:
        attachments = (
            PatientDocument.query.join(Patient,
                                       Patient.id == PatientDocument.patient_id)
            .filter(Patient.department_id == dept_id)
            .order_by(PatientDocument.uploaded_at.desc())
            .all())
    else:
        # No department mapping: fall back to the doctor's own patients.
        allowed = accessible_patient_ids(current_user) or set()
        if allowed:
            attachments = (
                PatientDocument.query.filter(
                    PatientDocument.patient_id.in_(sorted(allowed)))
                .order_by(PatientDocument.uploaded_at.desc()).limit(50).all())

    # Group by patient for an at-a-glance inbox view.
    by_patient = {}
    for doc in attachments:
        by_patient.setdefault(doc.patient_id, []).append(doc)

    # Department label for the header.
    dept_name = None
    if dept_id:
        dept = DeptModel.query.get(dept_id)
        dept_name = dept.name if dept else None

    return render_template(
        'doctor/attachments.html', title='Patient Attachments',
        doctor=doctor, attachments=attachments, by_patient=by_patient,
        dept_name=dept_name)


@doctor_bp.route('/patients')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def patients():
    search = request.args.get('q', '')
    query = Patient.query
    # A role reconciliation: Doctor only sees patients they have a documented
    # need-to-know relationship with, so opening a patient's overview/detail
    # never 403s. Admin/SuperAdmin return the full patient set from
    # accessible_patient_ids, so filtering is safe for every role.
    allowed = accessible_patient_ids(current_user)
    query = query.filter(Patient.id.in_(allowed)) if allowed \
        else query.filter(Patient.id.is_(None))
    if search:
        query = query.join(Patient.user).filter(
            db.or_(User.full_name.ilike(f'%{search}%'),
                   User.email.ilike(f'%{search}%')))
    results = query.limit(100).all()
    return render_template('doctor/patients.html', title='Patient Search',
                           patients=results, search=search)


@doctor_bp.route('/patients/<int:patient_id>/overview')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def patient_overview(patient_id):
    """Patient 360 view: demographics, latest vitals, active problems,
    current medications, recent labs/imaging, and clinical alerts in one page."""
    patient = Patient.query.get_or_404(patient_id)

    latest_vitals = VitalSign.query.filter_by(patient_id=patient.id) \
        .order_by(VitalSign.recorded_at.desc()).first()
    vitals_history = VitalSign.query.filter_by(patient_id=patient.id) \
        .order_by(VitalSign.recorded_at.desc()).limit(5).all()

    diagnoses = Diagnosis.query.filter_by(patient_id=patient.id) \
        .order_by(Diagnosis.date_diagnosed.desc()).all()
    records = MedicalRecord.query.filter_by(patient_id=patient.id) \
        .order_by(MedicalRecord.visit_date.desc()).limit(5).all()
    active_rxs = Prescription.query.filter_by(patient_id=patient.id, status='Active') \
        .order_by(Prescription.prescribed_date.desc()).all()
    all_rxs = Prescription.query.filter_by(patient_id=patient.id) \
        .order_by(Prescription.prescribed_date.desc()).limit(10).all()

    recent_labs = LabOrder.query.filter_by(patient_id=patient.id) \
        .order_by(LabOrder.order_date.desc()).limit(5).all()
    recent_imaging = RadiologyOrder.query.filter_by(patient_id=patient.id) \
        .order_by(RadiologyOrder.order_date.desc()).limit(5).all()
    attachments = PatientDocument.query.filter_by(patient_id=patient.id) \
        .order_by(PatientDocument.uploaded_at.desc()).all()

    alerts = []
    if patient.allergies:
        alerts.append({'level': 'danger', 'label': 'Allergy',
                       'detail': patient.allergies})
    if patient.chronic_diseases:
        alerts.append({'level': 'warning', 'label': 'Chronic',
                       'detail': patient.chronic_diseases})
    if latest_vitals and latest_vitals.blood_pressure_systolic and \
            latest_vitals.blood_pressure_systolic >= 140:
        alerts.append({'level': 'danger', 'label': 'Elevated BP',
                       'detail': f"{latest_vitals.blood_pressure_systolic}/"
                                 f"{latest_vitals.blood_pressure_diastolic}"})
    abnormal_labs = LabOrder.query.filter(LabOrder.patient_id == patient.id,
                                          LabOrder.result.has(is_abnormal=True)).limit(5).all()
    for o in abnormal_labs:
        if o.result:
            alerts.append({'level': 'warning', 'label': 'Abnormal Lab',
                           'detail': f"{o.test.test_name}: {o.result.result_value}"})

    image_attachments = [
        document for document in attachments
        if (document.document_type or '').lower() in {'imaging', 'clinical_image', 'radiology'}
        or (document.file_url or '').lower().endswith(('.png', '.jpg', '.jpeg'))
    ]
    image_ai_tools = []
    roles = {role.name for role in current_user.roles}
    if roles & {'Doctor', 'Radiologist', 'Nurse', 'Physiotherapist', 'Dentist', 'Admin', 'SuperAdmin'}:
        image_ai_tools.append(('fracture_detection', 'Fracture Detection', 'fa-bone'))
    if roles & {'Dentist', 'Radiologist', 'Nurse', 'Admin', 'SuperAdmin'}:
        image_ai_tools.append(('tooth_segmentation', 'Tooth Segmentation', 'fa-tooth'))
    if roles & {'Doctor', 'Dentist', 'Nurse', 'Admin', 'SuperAdmin'}:
        image_ai_tools.append(('skin_lesion_detection', 'AI Skin Lesion Detection', 'fa-person-circle-question'))

    return render_template('doctor/patient_overview.html',
                           title='Patient Overview', patient=patient,
                           latest_vitals=latest_vitals, vitals_history=vitals_history,
                           diagnoses=diagnoses, records=records,
                           active_rxs=active_rxs, all_rxs=all_rxs,
                           recent_labs=recent_labs, recent_imaging=recent_imaging,
                           attachments=attachments, image_attachments=image_attachments,
                           image_ai_tools=image_ai_tools, alerts=alerts, **patient_safety_context(patient.id),
                           today=utcnow().date())


@doctor_bp.route('/patients/<int:patient_id>')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def patient_detail(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    records = MedicalRecord.query.filter_by(patient_id=patient.id).order_by(
        MedicalRecord.visit_date.desc()).all()
    diagnoses = Diagnosis.query.filter_by(patient_id=patient.id).all()
    vitals = VitalSign.query.filter_by(patient_id=patient.id).order_by(
        VitalSign.recorded_at.desc()).limit(10).all()
    return render_template('doctor/patient_detail.html', title='Patient Record',
                            patient=patient, records=records, diagnoses=diagnoses,
                            vitals=vitals, **patient_safety_context(patient.id),
                            today=utcnow().date())


@doctor_bp.route('/patients/<int:patient_id>/360')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def patient_360(patient_id):
    """Patient 360° — unified clinical view with alerts and a timeline."""
    patient = Patient.query.get_or_404(patient_id)

    latest_vitals = VitalSign.query.filter_by(patient_id=patient.id) \
        .order_by(VitalSign.recorded_at.desc()).first()
    diagnoses = Diagnosis.query.filter_by(patient_id=patient.id) \
        .order_by(Diagnosis.date_diagnosed.desc()).all()
    active_rxs = Prescription.query.filter_by(patient_id=patient.id, status='Active') \
        .order_by(Prescription.prescribed_date.desc()).all()
    recent_labs = LabOrder.query.filter_by(patient_id=patient.id) \
        .order_by(LabOrder.order_date.desc()).limit(8).all()
    recent_imaging = RadiologyOrder.query.filter_by(patient_id=patient.id) \
        .order_by(RadiologyOrder.order_date.desc()).limit(8).all()
    upcoming = Appointment.query.filter_by(
        patient_id=patient.id, status='Scheduled') \
        .order_by(Appointment.scheduled_at).limit(5).all()
    attachments = PatientDocument.query.filter_by(patient_id=patient.id) \
        .order_by(PatientDocument.uploaded_at.desc()).all()

    alerts = []
    if patient.allergies:
        alerts.append({'level': 'danger', 'label': 'Allergy', 'detail': patient.allergies})
    if patient.chronic_diseases:
        alerts.append({'level': 'warning', 'label': 'Chronic', 'detail': patient.chronic_diseases})
    if latest_vitals and latest_vitals.blood_pressure_systolic \
            and latest_vitals.blood_pressure_systolic >= 140:
        alerts.append({'level': 'danger', 'label': 'Elevated BP',
                       'detail': f"{latest_vitals.blood_pressure_systolic}/"
                                 f"{latest_vitals.blood_pressure_diastolic}"})
    for o in recent_labs:
        if o.result and o.result.is_abnormal:
            alerts.append({'level': 'warning', 'label': 'Abnormal Lab',
                           'detail': f"{o.test.test_name if o.test else 'Lab'}: "
                                     f"{o.result.result_value}"})

    # Each timeline entry carries (time, icon, label, detail, badge-color,
    # specialty-name). The color + specialty make clear which discipline owns
    # the event, which is the whole idea of a unified record over per-portal
    # silos.
    timeline = []
    for a in Appointment.query.filter_by(patient_id=patient.id).all():
        timeline.append((a.scheduled_at, 'calendar-check', 'Appointment',
                         a.reason or a.status, 'info', 'Reception'))
    for r in MedicalRecord.query.filter_by(patient_id=patient.id).all():
        timeline.append((r.visit_date, 'file-medical', 'Consultation',
                         r.diagnosis or '', 'primary', 'Doctor'))
    for d in diagnoses:
        timeline.append((d.date_diagnosed, 'stethoscope', 'Diagnosis',
                         d.description, 'primary', 'Doctor'))
    for rx in Prescription.query.filter_by(patient_id=patient.id).all():
        timeline.append((rx.prescribed_date, 'pills', 'Prescription',
                         f"{len(rx.items)} item(s) — {rx.status}", 'success', 'Pharmacy'))
    for o in recent_labs:
        if o.result:
            timeline.append((o.result.result_date, 'flask', 'Lab result',
                             o.test.test_name if o.test else 'Lab', 'warning', 'Laboratory'))
    for o in recent_imaging:
        if o.report:
            timeline.append((o.report.report_date, 'x-ray', 'Radiology',
                             o.imaging_type.name if o.imaging_type else 'Imaging',
                             'secondary', 'Radiology'))
    for v in VitalSign.query.filter_by(patient_id=patient.id).all():
        timeline.append((v.recorded_at, 'heartbeat', 'Vitals recorded',
                         '', 'info', 'Nursing'))

    bill_records = Bill.query.filter_by(patient_id=patient.id).all()
    for b in bill_records:
        timeline.append((
            b.issued_at, 'receipt', 'Bill',
            f'{b.bill_no} — {b.status} ({b.balance():.2f} remaining)',
            'danger' if b.status in ('Unpaid', 'PartiallyPaid') else 'success',
            'Billing'))
    bills_total = sum(b.total() for b in bill_records)
    bills_balance = sum(b.balance() for b in bill_records)

    timeline = [t for t in timeline if t[0]]
    timeline.sort(key=lambda x: x[0], reverse=True)

    image_attachments = [
        document for document in attachments
        if (document.document_type or '').lower() in {'imaging', 'clinical_image', 'radiology'}
        or (document.file_url or '').lower().endswith(('.png', '.jpg', '.jpeg'))
    ]
    image_ai_tools = []
    roles = {role.name for role in current_user.roles}
    if roles & {'Doctor', 'Radiologist', 'Nurse', 'Physiotherapist', 'Dentist', 'Admin', 'SuperAdmin'}:
        image_ai_tools.append(('fracture_detection', 'Fracture Detection', 'fa-bone'))
    if roles & {'Dentist', 'Radiologist', 'Nurse', 'Admin', 'SuperAdmin'}:
        image_ai_tools.append(('tooth_segmentation', 'Tooth Segmentation', 'fa-tooth'))
    if roles & {'Doctor', 'Dentist', 'Nurse', 'Admin', 'SuperAdmin'}:
        image_ai_tools.append(('skin_lesion_detection', 'AI Skin Lesion Detection', 'fa-person-circle-question'))

    return render_template(
        'doctor/patient_360.html', title='Patient 360', patient=patient,
        user=patient.user, latest_vitals=latest_vitals, diagnoses=diagnoses,
        active_rxs=active_rxs, recent_labs=recent_labs, recent_imaging=recent_imaging,
        upcoming=upcoming, attachments=attachments, image_attachments=image_attachments,
        image_ai_tools=image_ai_tools, alerts=alerts, timeline=timeline[:30],
        bills_total=bills_total, bills_balance=bills_balance,
        **patient_safety_context(patient.id), today=utcnow().date())


@doctor_bp.route('/patients/medical_documents/<path:filename>')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def view_medical_document(filename):
    """Display a private patient attachment inline at the legacy doctor URL."""
    relative_url = f'medical_documents/{filename}'
    document = PatientDocument.query.filter_by(file_url=relative_url).first()
    if not document:
        document = PatientDocument.query.filter_by(file_url=f'/{relative_url}').first()
    if not document:
        abort(404)
    require_patient_access(document.patient)

    upload_root = os.path.abspath(
        current_app.config.get('UPLOAD_FOLDER') or 'var/uploads')
    path = os.path.abspath(os.path.join(upload_root, relative_url))
    if os.path.commonpath((upload_root, path)) != upload_root or not os.path.isfile(path):
        abort(404)
    content_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    return send_file(path, mimetype=content_type, as_attachment=False,
                     download_name=os.path.basename(path))


@doctor_bp.route('/patients/<int:patient_id>/emr/add', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def add_emr(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    doctor = _current_doctor()

    def _usable_template(tpl):
        if not tpl or not tpl.is_active:
            return None
        if not template_allowed_for(tpl, current_user):
            return None
        return tpl

    template = None
    sections = []
    if request.method == 'POST':
        tpl_id = request.form.get('template_id')
        if tpl_id:
            template = _usable_template(
                ClinicianTemplate.query.get(int(tpl_id)))
        if template:
            try:
                sections = normalize_sections(template.section_list)
            except ValueError:
                sections = []
        clinical_notes = request.form.get('clinical_notes')
        if sections:
            values = {}
            for s in sections:
                if s['type'] == 'checkbox':
                    values[s['key']] = request.form.getlist('field__' + s['key'])
                else:
                    values[s['key']] = request.form.get('field__' + s['key'])
            structured = format_structured_notes(sections, values)
            if structured:
                clinical_notes = (clinical_notes + '\n\n' + structured) \
                    if clinical_notes else structured
        record = MedicalRecord(
            patient_id=patient.id,
            doctor_id=doctor.id if doctor else None,
            diagnosis=request.form.get('diagnosis'),
            treatment_plan=request.form.get('treatment_plan'),
            clinical_notes=clinical_notes,
        )
        db.session.add(record)
        if request.form.get('icd10') or request.form.get('diagnosis'):
            db.session.add(Diagnosis(
                patient_id=patient.id,
                doctor_id=doctor.id if doctor else None,
                icd10_code=request.form.get('icd10'),
                description=request.form.get('diagnosis') or 'Clinical note',
            ))
        log_activity('ADD_EMR', 'medical_record', record.id,
                     f'patient={patient.id}')
        record_event(patient.id, 'DIAGNOSIS',
                     f'Consultation record: {record.diagnosis or "Clinical note"}',
                     record.clinical_notes or '',
                     source_type='medical_record', source_id=record.id,
                     department='Doctor')
        db.session.commit()
        flash('Medical record added.', 'success')
        return redirect(url_for('doctor.patient_detail', patient_id=patient.id))

    tpl_id = request.args.get('template')
    if tpl_id:
        template = _usable_template(ClinicianTemplate.query.get(int(tpl_id)))
    if template:
        try:
            sections = normalize_sections(template.section_list)
        except ValueError:
            sections = []
    templates = [t for t in ClinicianTemplate.query
                 .order_by(ClinicianTemplate.title.asc()).all()
                 if _usable_template(t)]
    return render_template('doctor/add_emr.html', title='Add Medical Record',
                           patient=patient, **patient_safety_context(patient.id),
                           today=utcnow().date(), template=template,
                           sections=sections, templates=templates)


@doctor_bp.route('/records/<int:record_id>/sign', methods=['POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@permissions_required('MEDICAL_RECORD_SIGN')
def sign_record(record_id):
    """Sign/lock a medical record. Signed records become immutable until amended."""
    record = MedicalRecord.query.get_or_404(record_id)
    require_patient_access(record.patient)
    if is_clinical_locked(record):
        flash('This record is already signed/locked.', 'info')
        return redirect(url_for('doctor.patient_detail', patient_id=record.patient_id))
    record.status = 'Signed'
    record.signed_by = current_user.id
    record.signed_at = utcnow()
    log_activity('SIGN_MEDICAL_RECORD', 'medical_record', record.id,
                 f'Signed by {current_user.full_name}')
    if record.patient:
        from app.services.notifications import notify_patient
        notify_patient(record.patient, 'Medical record signed',
                       'Your medical record has been signed by the attending physician.')
    db.session.commit()
    flash('Medical record signed and locked.', 'success')
    return redirect(url_for('doctor.patient_detail', patient_id=record.patient_id))


@doctor_bp.route('/records/<int:record_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@permissions_required('MEDICAL_RECORD_AMEND')
def edit_emr(record_id):
    """Edit an existing record; amending a signed record requires a reason and
    re-signing (clinical records are otherwise immutable)."""
    record = MedicalRecord.query.get_or_404(record_id)
    require_patient_access(record.patient)
    reason = request.form.get('reason') if request.method == 'POST' else None
    if request.method == 'POST' and is_clinical_locked(record) and not reason:
        flash('This record is signed and locked. Provide a reason to amend it.', 'warning')
        return redirect(url_for('doctor.edit_emr', record_id=record.id))
    if request.method == 'POST':
        old_state = {
            'diagnosis': record.diagnosis, 'treatment_plan': record.treatment_plan,
            'clinical_notes': record.clinical_notes, 'status': record.status,
        }
        record.diagnosis = request.form.get('diagnosis')
        record.treatment_plan = request.form.get('treatment_plan')
        record.clinical_notes = request.form.get('clinical_notes')
        if is_clinical_locked(record):
            record.status = 'Draft'  # must be re-signed after amendment
            record.signed_by = None
            record.signed_at = None
            log_change('AMEND_MEDICAL_RECORD', 'medical_record', record.id,
                       old_value=old_state,
                       new_value={'diagnosis': record.diagnosis,
                                  'treatment_plan': record.treatment_plan,
                                  'clinical_notes': record.clinical_notes,
                                  'status': record.status},
                       reason=reason or 'No reason provided',
                       details=f'patient={record.patient_id}')
            flash('Amendment recorded; record reopened for re-signing.', 'success')
        else:
            log_activity('EDIT_MEDICAL_RECORD', 'medical_record', record.id)
            flash('Medical record updated.', 'success')
        db.session.commit()
        return redirect(url_for('doctor.patient_detail', patient_id=record.patient_id))
    return render_template('doctor/edit_emr.html', title='Edit Medical Record',
                           patient=record.patient, record=record,
                           **patient_safety_context(record.patient_id),
                           today=utcnow().date())


@doctor_bp.route('/patients/<int:patient_id>/prescriptions', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def prescriptions(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    doctor = _current_doctor()
    if request.method == 'POST':
        rx = Prescription(
            patient_id=patient.id,
            doctor_id=doctor.id if doctor else None,
            refills=int(request.form.get('refills') or 0),
        )
        db.session.add(rx)
        db.session.flush()

        med_ids = request.form.getlist('medication_id')
        dosages = request.form.getlist('dosage')
        frequencies = request.form.getlist('frequency')
        durations = request.form.getlist('duration')
        instructions = request.form.getlist('instructions')
        quantities = request.form.getlist('quantity')

        added = 0
        for i, mid in enumerate(med_ids):
            if not mid:
                continue
            qty = quantities[i] if i < len(quantities) else 1
            try:
                qty = max(1, int(qty))
            except (TypeError, ValueError):
                qty = 1
            db.session.add(PrescriptionItem(
                prescription_id=rx.id,
                medication_id=int(mid),
                dosage=dosages[i] if i < len(dosages) else '',
                frequency=frequencies[i] if i < len(frequencies) else '',
                duration=durations[i] if i < len(durations) else '',
                instructions=instructions[i] if i < len(instructions) else '',
                quantity=qty,
            ))
            added += 1

        if added == 0:
            db.session.rollback()
            flash('Add at least one medication to the prescription.', 'danger')
            return redirect(url_for('doctor.prescriptions', patient_id=patient.id))

        log_activity('CREATE_PRESCRIPTION', 'prescription', rx.id,
                     f'patient={patient.id} items={added}')
        record_event(patient.id, 'PRESCRIPTION',
                     f'Prescription written ({added} item(s))',
                     f'Refills: {rx.refills}',
                     source_type='prescription', source_id=rx.id,
                     department='Doctor')
        # Clinical safety screens immediately: allergy conflicts and
        # interactions are flagged as open alerts for the review teams.
        flag_prescription_safety(rx)
        # Route the prescription into the pharmacy work queue (commit together
        # so the task is never rolled back by a notification failure).
        task = None
        from app.services import tasks as task_svc
        task = task_svc.create_task(
            title=f'Dispense prescription #{rx.id}',
            description=f'Review and dispense {added} item(s). Check interactions and stock.',
            task_type='PHARMACY', department='Pharmacy',
            patient_id=patient.id, assigned_role='Pharmacist',
            priority='NORMAL', related_resource_type='prescription',
            related_resource_id=rx.id)
        db.session.commit()
        try:
            task_svc.notify_task_activity(task)
            from app.services.notifications import notify_role
            notify_role('Pharmacist',
                        f'New prescription #{rx.id}',
                        f'A new prescription with {added} item(s) was written for patient #{patient.id}.',
                        entity_type='prescription', entity_id=rx.id)
            db.session.commit()
        except Exception:
            db.session.rollback()
            db.session.commit()
        flash('Prescription created.', 'success')
        return redirect(url_for('doctor.prescriptions', patient_id=patient.id))
    meds = Medication.query.all()
    prescriptions = Prescription.query.filter_by(patient_id=patient.id).all()
    return render_template('doctor/prescriptions.html', title='Prescriptions',
                           patient=patient, meds=meds, items=prescriptions,
                           **patient_safety_context(patient.id),
                           today=utcnow().date())


@doctor_bp.route('/prescriptions/<int:rx_id>/cancel', methods=['POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@permissions_required('PRESCRIPTION_CREATE')
def cancel_prescription(rx_id):
    """Cancel an Active prescription. Once any item is dispensed it becomes
    locked and can no longer be cancelled (must go through the pharmacy)."""
    rx = Prescription.query.get_or_404(rx_id)
    require_patient_access(rx.patient)
    if rx.status == 'Cancelled':
        flash('Prescription already cancelled.', 'info')
    elif rx.status == 'Dispensed' or rx.dispensed():
        flash('Cannot cancel: medication has already been dispensed.', 'danger')
    else:
        old = rx.status
        rx.status = 'Cancelled'
        for item in rx.items:
            if item.status != 'Dispensed':
                item.status = 'Cancelled'
        log_change('CANCEL_PRESCRIPTION', 'prescription', rx.id,
                   old_value={'status': old}, new_value={'status': 'Cancelled'},
                   reason=request.form.get('reason') or 'Cancelled by physician',
                   details=f'patient={rx.patient_id}')
        db.session.commit()
        flash('Prescription cancelled.', 'success')
    return redirect(url_for('doctor.prescriptions', patient_id=rx.patient_id))


@doctor_bp.route('/patients/<int:patient_id>/lab-order', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def lab_order(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    doctor = _current_doctor()
    if request.method == 'POST':
        test_id = request.form.get('test_id')
        if not test_id or not LabTestCatalog.query.get(int(test_id)):
            flash('Please select a valid test.', 'warning')
            return redirect(url_for('doctor.lab_order', patient_id=patient.id))
        order = LabOrder(
            patient_id=patient.id,
            doctor_id=doctor.id if doctor else None,
            test_id=int(test_id),
            priority=request.form.get('priority', 'Normal'),
            notes=request.form.get('notes'),
        )
        db.session.add(order)
        db.session.flush()
        order.accession_number = f'LAB-{order.id:05d}'
        order.barcode = f'{order.id:08d}'
        # Route the order into the shared lab work queue the same way the lab
        # blueprint does, so it is never dropped into the void.
        from app.services import tasks as task_svc
        from app.services.notifications import notify_role
        task_svc.create_task(
            title=f'Process lab order #{order.id}: {order.test.test_name if order.test else ""}',
            description='Collect and process sample; enter and verify the result.',
            task_type='LAB', department='Laboratory',
            patient_id=patient.id, assigned_role='LabTechnician',
            priority=order.priority, related_resource_type='lab_order',
            related_resource_id=order.id)
        record_event(patient.id, 'LAB',
                     f'Lab order: {order.test.test_name if order.test else "Test"}',
                     f'{request.form.get("specimen_type") or "Blood"} sample · priority {order.priority}',
                     source_type='lab_order', source_id=order.id,
                     department='Laboratory')
        notify_role('LabTechnician',
                    f'New lab order #{order.id}',
                    f'A new lab order ({order.test.test_name if order.test else ""}) has been created for patient #{patient.id}.',
                    entity_type='lab_order', entity_id=order.id)
        log_activity('REQUEST_LAB', 'lab_order', order.id,
                     f'patient={patient.id} test={test_id}')
        db.session.commit()
        flash('Lab order requested and routed to the lab queue.', 'success')
        return redirect(url_for('doctor.patient_detail', patient_id=patient.id))
    tests = LabTestCatalog.query.filter_by(is_active=True).all()
    return render_template('doctor/lab_order.html', title='Lab Order',
                           patient=patient, tests=tests,
                           **patient_safety_context(patient.id),
                           today=utcnow().date())


@doctor_bp.route('/patients/<int:patient_id>/radiology-order', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def radiology_order(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    doctor = _current_doctor()
    if request.method == 'POST':
        imaging_type_id = request.form.get('imaging_type_id')
        if not imaging_type_id or not ImagingType.query.get(int(imaging_type_id)):
            flash('Please select a valid imaging type.', 'warning')
            return redirect(url_for('doctor.radiology_order', patient_id=patient.id))
        order = RadiologyOrder(
            patient_id=patient.id,
            doctor_id=doctor.id if doctor else None,
            imaging_type_id=int(imaging_type_id),
            priority=request.form.get('priority', 'Normal'),
            notes=request.form.get('notes'),
        )
        db.session.add(order)
        db.session.flush()
        from app.services import tasks as task_svc
        from app.services.notifications import notify_role
        task_svc.create_task(
            title=f'Perform study #{order.id}: {order.imaging_type.name if order.imaging_type else ""}',
            description='Schedule, capture, and prepare the study for reporting.',
            task_type='RADIOLOGY', department='Radiology',
            patient_id=patient.id, assigned_role='Radiologist',
            priority=order.priority, related_resource_type='radiology_order',
            related_resource_id=order.id)
        record_event(patient.id, 'RADIOLOGY',
                     f'Imaging ordered: {order.imaging_type.name if order.imaging_type else "Study"}',
                     f'Priority {order.priority}',
                     source_type='radiology_order', source_id=order.id,
                     department='Radiology')
        notify_role('Radiologist',
                    f'New radiology order #{order.id}',
                    f'A new imaging order ({order.imaging_type.name if order.imaging_type else ""}) has been created for patient #{patient.id}.',
                    entity_type='radiology_order', entity_id=order.id)
        log_activity('REQUEST_RADIOLOGY', 'radiology_order', order.id,
                     f'patient={patient.id} imaging={imaging_type_id}')
        db.session.commit()
        flash('Radiology order requested and routed to the imaging queue.', 'success')
        return redirect(url_for('doctor.patient_detail', patient_id=patient.id))
    imaging = ImagingType.query.all()
    return render_template('doctor/radiology_order.html', title='Radiology Order',
                           patient=patient, imaging=imaging,
                           **patient_safety_context(patient.id),
                           today=utcnow().date())


@doctor_bp.route('/appointments')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def appointments():
    doctor = _current_doctor()
    items = Appointment.query.filter_by(doctor_id=doctor.id).order_by(
        Appointment.scheduled_at.desc()).all() if doctor else []
    return render_template('doctor/appointments.html', title='My Appointments', items=items)


@doctor_bp.route('/appointments/<int:appt_id>/complete', methods=['POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def complete_appointment(appt_id):
    """Close out a visit: mark the appointment Completed or NoShow.

    When Completed, the consultation fee is pushed into billing automatically
    (one bill per appointment) so front desk collects on the visit."""
    appt = Appointment.query.get_or_404(appt_id)
    mode = request.form.get('mode', 'Completed')
    if mode not in ('Completed', 'NoShow'):
        mode = 'Completed'
    appt.status = mode
    log_activity('COMPLETE_APPOINTMENT', 'appointment', appt.id,
                 f'mode={mode} patient={appt.patient_id}')
    if mode == 'Completed':
        record_event(appt.patient_id, 'VISIT',
                     'Visit completed',
                     f'Appointment #{appt.id} · {appt.visit_type or "Scheduled"}',
                     source_type='appointment', source_id=appt.id,
                     department='Doctor')
        from app.services.billing import ensure_bill_for_consultation
        bill = ensure_bill_for_consultation(appt.id, appt.patient_id, appt.doctor_id)
        if bill:
            log_activity('AUTO_BILL_CONSULTATION', 'bill', bill.id,
                         f'appointment={appt.id} total={bill.total():.2f}')
    db.session.commit()
    if mode == 'Completed':
        flash('Appointment marked completed. Consultation bill generated.', 'success')
    else:
        flash('Appointment marked as no-show.', 'warning')
    return redirect(url_for('doctor.appointments'))


@doctor_bp.route('/lab-results')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def lab_results():
    doctor = _current_doctor()
    orders = LabOrder.query.filter_by(doctor_id=doctor.id).order_by(
        LabOrder.order_date.desc()).all() if doctor else []
    return render_template('doctor/lab_results.html', title='Lab Results', orders=orders)
