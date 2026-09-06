"""Clinical workbench blueprint.

A single cross-department window onto a patient's record: the unified timeline,
clinical alerts, structured allergies/problems/immunizations/follow-ups, plus
the shared clinical documents. Guards every route with the record-level
permissions from ``app/permissions.py`` and enforces need-to-know on the
patient at all times.
"""
import json

from flask import abort, Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app import db
from app.access import (accessible_patient_ids, patient_access_required,
                        require_patient_access)
from app.models import (AuditLog, Admission, Allergy, CareTeam, CareTeamMember, ClinicalAlert,
                        ClinicianTemplate, ClinicalReminder, Department, Doctor,
                        FollowUp, ImagingType, ImmunizationRecord, LabOrder, LabResult,
                        LabTestCatalog, MedicalRecord, Medication, OrderSet,
                        OrderSetItem, Patient, PatientDocument, PharmacyIntervention,
                        Prescription, PrescriptionItem, Problem, RadiologyOrder,
                        RadiologyReport, Referral, ResultAcknowledgement, Specialty,
                        Task, TimelineEvent, User, VitalSign)
from app.permissions import (ALERT_ACK, ALERT_VIEW, ALLERGY_CREATE,
                             ALLERGY_EDIT, CLINICAL_SUMMARY_VIEW, FOLLOWUP_COMPLETE,
                             FOLLOWUP_CREATE, FOLLOWUP_VIEW, IMMUNIZATION_CREATE,
                             IMMUNIZATION_VIEW, INBOX_VIEW, ORDER_SET_CREATE,
                             ORDER_SET_EDIT, ORDER_SET_VIEW, PROBLEM_CREATE,
                             PROBLEM_EDIT, PROBLEM_VIEW, REMINDER_ACK,
                             REMINDER_VIEW, RESULT_ACK, TEMPLATE_CREATE,
                             TEMPLATE_EDIT, TEMPLATE_VIEW, TIMELINE_VIEW)
from app.routes.decorators import log_activity, permissions_required
from app.services import alerts as alert_svc
from app.services.patient_safety import patient_safety_context
from app.services.timeline import record_event, merged_timeline
from app.utils import utcnow

clinical_bp = Blueprint('clinical', __name__)

CLINICAL_ROLES = ('Doctor', 'Nurse', 'Pharmacist', 'LabTechnician',
                  'Radiologist', 'Dentist', 'Physiotherapist', 'Admin',
                  'SuperAdmin')


def _doctor():
    return Doctor.query.filter_by(user_id=current_user.id).first()


def _ai_image_tools():
    """AI image-analysis tools the current user is allowed to run, used to build
    per-document "Analyze" actions in the patient record."""
    roles = {r.name for r in current_user.roles}
    tools = []
    if roles & {'Doctor', 'Dentist', 'Nurse', 'Admin', 'SuperAdmin'}:
        tools.append({'id': 'skin', 'label': 'Skin Lesion',
                      'label_ar': 'آفات الجلد',
                      'endpoint': 'ai.skin_lesion_detection',
                      'icon': 'fa-person-rays'})
    if roles & {'Dentist', 'Radiologist', 'Nurse', 'Admin', 'SuperAdmin'}:
        tools.append({'id': 'tooth', 'label': 'Tooth Segmentation',
                      'label_ar': 'تقسيم الأسنان',
                      'endpoint': 'ai.tooth_segmentation',
                      'icon': 'fa-tooth'})
    if roles & {'Radiologist', 'Doctor', 'Nurse', 'Physiotherapist',
                'Dentist', 'Admin', 'SuperAdmin'}:
        tools.append({'id': 'fracture', 'label': 'Fracture Detection',
                      'label_ar': 'كشف الكسور',
                      'endpoint': 'ai.fracture_detection',
                      'icon': 'fa-bone'})
    return tools


@clinical_bp.route('')
@login_required
@permissions_required(TIMELINE_VIEW)
def workbench():
    pids = accessible_patient_ids(current_user)
    patients = (Patient.query.filter(Patient.id.in_(pids or [-1]))
                .order_by(Patient.id.desc()).limit(50).all())
    rows = []
    id_list = [p.id for p in patients] or [-1]
    open_map = {}
    top_map = {}
    for alert in (ClinicalAlert.query
                  .filter(ClinicalAlert.patient_id.in_(id_list),
                          ClinicalAlert.status.in_(('OPEN', 'ACKNOWLEDGED')))
                  .all()):
        open_map[alert.patient_id] = open_map.get(alert.patient_id, 0) + 1
        if alert.status == 'OPEN':
            cur = top_map.get(alert.patient_id)
            if cur is None or (alert_svc.SEVERITY_WEIGHT.get(alert.severity, 0)
                               > alert_svc.SEVERITY_WEIGHT.get(cur.severity, 0)):
                top_map[alert.patient_id] = alert
    for p in patients:
        rows.append({'patient': p, 'open_alerts': open_map.get(p.id, 0),
                     'top_alert': top_map.get(p.id)})
    upcoming = FollowUp.query.filter(
        FollowUp.patient_id.in_(id_list),
        FollowUp.scheduled_for >= utcnow(),
        FollowUp.status == 'Scheduled').order_by(FollowUp.scheduled_for).limit(5).all()
    return render_template('clinical/workbench.html', title='Clinical Workbench',
                           rows=rows, body_class='',
                           open_alert_count=sum(r['open_alerts'] for r in rows),
                           upcoming=upcoming)


@clinical_bp.route('/alerts')
@login_required
@permissions_required(ALERT_VIEW)
def alerts_all():
    pids = accessible_patient_ids(current_user)
    q = ClinicalAlert.query.filter(ClinicalAlert.patient_id.in_(pids or [-1]))
    f_sev = (request.args.get('severity') or '').upper()
    f_status = (request.args.get('status') or '').upper()
    if f_sev == 'CRITICAL':
        q = q.filter(ClinicalAlert.severity.in_(('CRITICAL', 'HIGH')))
    elif f_sev in alert_svc.SEVERITIES:
        q = q.filter(ClinicalAlert.severity == f_sev)
    if f_status == 'ACTIVE':
        q = q.filter(ClinicalAlert.status.in_(alert_svc.ACTIVE_STATUSES))
    elif f_status in alert_svc.STATUSES:
        q = q.filter(ClinicalAlert.status == f_status)
    alerts = q.order_by(ClinicalAlert.created_at.desc()).limit(100).all()
    return render_template('clinical/alerts.html', title='Clinical Alerts',
                           alerts=alerts, f_sev=f_sev, f_status=f_status)


@clinical_bp.route('/alerts/<int:alert_id>', methods=['GET', 'POST'])
@login_required
@permissions_required(ALERT_VIEW)
def alert_detail(alert_id):
    """Critical-alert lifecycle: OPEN -> ACKNOWLEDGED -> IN_PROGRESS -> RESOLVED
    (or DISMISSED with a mandatory reason for high severity). Every step is
    audited; the receipt trail (who was notified) is shown alongside."""
    from app.models import Notification, Task, RadiologyReport
    alert = ClinicalAlert.query.get_or_404(alert_id)
    require_patient_access(alert.patient)
    if request.method == 'POST':
        if not current_user.has_permission('ALERT_ACK'):
            abort(403)
        action = request.form.get('action')
        note = (request.form.get('note') or '').strip() or None
        action_taken = (request.form.get('action_taken') or '').strip() or None
        try:
            if action == 'acknowledge':
                alert_svc.acknowledge(alert)
                log_activity('ALERT_ACK', 'clinical_alert', alert.id)
            elif action == 'start':
                alert_svc.start_progress(alert)
                log_activity('ALERT_IN_PROGRESS', 'clinical_alert', alert.id)
            elif action == 'resolve':
                alert_svc.resolve(alert, note, action_taken=action_taken)
                log_activity('ALERT_RESOLVE', 'clinical_alert', alert.id, action_taken or note)
                from app.services import tasks as task_svc
                task_svc.complete_for_resource('clinical_alert', alert.id, 'Alert resolved')
                record_event(alert.patient_id, 'ALERT', f'Alert resolved: {alert.title}',
                             action_taken or note, source_type='clinical_alert', source_id=alert.id,
                             department='Clinical')
            elif action == 'dismiss':
                alert_svc.dismiss(alert, note)
                log_activity('ALERT_DISMISS', 'clinical_alert', alert.id, note)
                from app.services import tasks as task_svc
                task_svc.cancel_for_resource('clinical_alert', alert.id, 'Alert dismissed')
            else:
                flash('Unknown alert action.', 'warning')
                return redirect(url_for('clinical.alert_detail', alert_id=alert.id))
            db.session.commit()
            flash('Alert updated.', 'success')
        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'warning')
        return redirect(url_for('clinical.alert_detail', alert_id=alert.id))
    receipts = (Notification.query.filter_by(entity_type='clinical_alert', entity_id=alert.id)
                .order_by(Notification.created_at.asc()).all())
    tasks = (Task.query.filter_by(related_resource_type='clinical_alert', related_resource_id=alert.id)
             .order_by(Task.created_at.desc()).all())
    report = None
    if alert.source_type == 'radiology_report' and alert.source_id:
        report = RadiologyReport.query.get(alert.source_id)
    from app.models import AuditLog, User
    audit = []
    for e in (AuditLog.query.filter_by(resource='clinical_alert', resource_id=alert.id)
              .order_by(AuditLog.created_at.asc()).all()):
        u = db.session.get(User, e.user_id) if e.user_id else None
        audit.append({'action': e.action, 'details': e.details, 'created_at': e.created_at,
                      'user_name': u.full_name if u else 'system'})
    return render_template('clinical/alert_detail.html', title='Clinical Alert', alert=alert,
                           receipts=receipts, tasks=tasks, report=report, audit=audit,
                           patient=alert.patient, **patient_safety_context(alert.patient_id))


@clinical_bp.route('/patient/<int:patient_id>')
@login_required
@permissions_required(TIMELINE_VIEW)
@patient_access_required
def patient_360(patient_id):
    patient = Patient.query.get(patient_id)
    if patient is None:
        flash('Patient not found.', 'warning')
        return redirect(url_for('clinical.workbench'))
    timeline = merged_timeline(patient_id, limit=120)
    safety = patient_safety_context(patient_id)
    allergies = safety['allergies']
    problems = safety['problems']
    open_alerts = safety['open_alerts']
    active_meds = safety['active_meds']
    immunizations = ImmunizationRecord.query.filter_by(patient_id=patient_id) \
        .order_by(ImmunizationRecord.administered_at.desc()).all()
    follow_ups = FollowUp.query.filter_by(patient_id=patient_id) \
        .order_by(FollowUp.scheduled_for.desc()).all()
    documents = PatientDocument.query.filter_by(patient_id=patient_id) \
        .order_by(PatientDocument.uploaded_at.desc()).all()
    doctors = Doctor.query.join(User, Doctor.user_id == User.id).all()
    # Latest vitals for the care strip
    latest_vitals = (VitalSign.query.filter_by(patient_id=patient_id)
                     .order_by(VitalSign.recorded_at.desc()).limit(10).all())
    # Active admission
    active_admission = Admission.query.filter_by(
        patient_id=patient_id, status='Admitted').first()
    # Cross-department snapshot so the hub links to every source record.
    recent_labs = (LabOrder.query.filter_by(patient_id=patient_id)
                   .order_by(LabOrder.order_date.desc()).limit(6).all())
    recent_imaging = (RadiologyOrder.query.filter_by(patient_id=patient_id)
                      .order_by(RadiologyOrder.order_date.desc()).limit(6).all())
    prescriptions = (Prescription.query.filter_by(patient_id=patient_id)
                     .order_by(Prescription.prescribed_date.desc()).limit(6).all())
    encounters = (MedicalRecord.query.filter_by(patient_id=patient_id)
                  .order_by(MedicalRecord.visit_date.desc()).limit(6).all())
    referrals = (Referral.query.filter_by(patient_id=patient_id)
                 .order_by(Referral.created_at.desc()).limit(6).all())
    open_tasks = (Task.query.filter(Task.patient_id == patient_id,
                                    Task.status.in_(('NEW', 'ASSIGNED', 'IN_PROGRESS', 'ON_HOLD')))
                  .order_by(Task.created_at.desc()).limit(8).all())
    from app.models import (Appointment, Bill, TherapySession, TherapyPlan, DentalProcedure,
                            DentalTreatmentPlan, NursingNote, CarePlan,
                            MedicationAdministration, Admission as AdmissionModel)
    appointments = (Appointment.query.filter_by(patient_id=patient_id)
                    .order_by(Appointment.scheduled_at.desc()).limit(5).all())
    bills = Bill.query.filter_by(patient_id=patient_id).order_by(Bill.issued_at.desc()).limit(5).all()
    balance_due = sum(b.balance() for b in Bill.query.filter_by(patient_id=patient_id).all()
                      if b.status in ('Unpaid', 'PartiallyPaid'))
    therapy = (TherapySession.query.filter_by(patient_id=patient_id)
               .order_by(TherapySession.scheduled_at.desc()).limit(4).all())
    therapy_plans = TherapyPlan.query.filter_by(patient_id=patient_id).order_by(TherapyPlan.id.desc()).limit(3).all()
    dental = (DentalProcedure.query.filter_by(patient_id=patient_id)
              .order_by(DentalProcedure.performed_at.desc()).limit(4).all())
    dental_plans = DentalTreatmentPlan.query.filter_by(patient_id=patient_id).order_by(DentalTreatmentPlan.id.desc()).limit(3).all()
    nursing_notes = (NursingNote.query.filter_by(patient_id=patient_id)
                     .order_by(NursingNote.created_at.desc()).limit(4).all())
    care_plans = CarePlan.query.filter_by(patient_id=patient_id).order_by(CarePlan.start_date.desc()).limit(3).all()
    mar_recent = (MedicationAdministration.query.filter_by(patient_id=patient_id)
                  .order_by(MedicationAdministration.created_at.desc()).limit(5).all())
    admissions = (AdmissionModel.query.filter_by(patient_id=patient_id)
                  .order_by(AdmissionModel.admitted_at.desc()).limit(4).all())
    care_members = (CareTeamMember.query
                    .join(CareTeam, CareTeamMember.team_id == CareTeam.id)
                    .filter(CareTeam.patient_id == patient_id).all())
    diagnoses = (db.session.query(MedicalRecord).filter_by(patient_id=patient_id).count())
    is_doctor_like = current_user.has_any_role('Doctor', 'Admin', 'SuperAdmin')
    return render_template(
        'clinical/patient_360.html', title=f'Clinical - {patient.user.full_name}',
        patient=patient, timeline=timeline, open_alerts=open_alerts,
        allergies=allergies, problems=problems, immunizations=immunizations,
        follow_ups=follow_ups, documents=documents, doctors=doctors,
        latest_vitals=latest_vitals, active_admission=active_admission,
        ai_tools=_ai_image_tools(),
        recent_labs=recent_labs, recent_imaging=recent_imaging,
        prescriptions=prescriptions, encounters=encounters, referrals=referrals,
        open_tasks=open_tasks, appointments=appointments, bills=bills,
        balance_due=balance_due, therapy=therapy, therapy_plans=therapy_plans,
        dental=dental, dental_plans=dental_plans, nursing_notes=nursing_notes,
        care_plans=care_plans, mar_recent=mar_recent, admissions=admissions,
        care_members=care_members, is_doctor_like=is_doctor_like,
        today=utcnow().date(), active_meds=active_meds)


# ------------------------- Alerts workflow -------------------------
@clinical_bp.route('/patient/<int:patient_id>/alerts/<int:alert_id>/<action>',
                   methods=['POST'])
@login_required
@permissions_required(ALERT_ACK)
@patient_access_required
def alert_action(patient_id, alert_id, action):
    alert = ClinicalAlert.query.get(alert_id)
    if alert is None or alert.patient_id != patient_id:
        flash('Alert not found.', 'warning')
        return redirect(url_for('clinical.patient_360', patient_id=patient_id))
    note = request.form.get('note') or None
    try:
        if action == 'ack':
            alert_svc.acknowledge(alert)
            log_activity('ALERT_ACK', 'clinical_alert', alert.id)
        elif action == 'resolve':
            alert_svc.resolve(alert, note)
            log_activity('ALERT_RESOLVE', 'clinical_alert', alert.id, note)
        elif action == 'dismiss':
            alert_svc.dismiss(alert, note)
            log_activity('ALERT_DISMISS', 'clinical_alert', alert.id, note)
        else:
            flash('Unknown alert action.', 'warning')
            return redirect(url_for('clinical.patient_360', patient_id=patient_id))
        db.session.commit()
        flash('Alert updated.', 'success')
    except ValueError as e:
        flash(str(e), 'warning')
    return redirect(url_for('clinical.patient_360', patient_id=patient_id))


# ------------------------- Structured allergies -------------------------
@clinical_bp.route('/patient/<int:patient_id>/allergies/new', methods=['POST'])
@login_required
@permissions_required(ALLERGY_CREATE)
@patient_access_required
def new_allergy(patient_id):
    patient = Patient.query.get(patient_id)
    if patient is None:
        flash('Patient not found.', 'warning')
        return redirect(url_for('clinical.workbench'))
    substance = (request.form.get('substance') or '').strip()
    if not substance:
        flash('Substance is required.', 'warning')
        return redirect(url_for('clinical.patient_360', patient_id=patient_id))
    allergy = Allergy(
        patient_id=patient_id, substance=substance,
        reaction=request.form.get('reaction') or None,
        severity=request.form.get('severity') or 'Moderate',
        onset=request.form.get('onset') or None,
        recorded_by=current_user.id,
        notes=request.form.get('notes') or None,
    )
    db.session.add(allergy)
    db.session.flush()
    record_event(patient_id, 'ALLERGY',
                 f'Allergy recorded: {allergy.substance}',
                 allergy.reaction, department='Clinical')
    log_activity('CREATE_ALLERGY', 'allergy', allergy.id, substance)
    db.session.commit()
    flash('Allergy recorded.', 'success')
    return redirect(url_for('clinical.patient_360', patient_id=patient_id))


@clinical_bp.route('/patient/<int:patient_id>/allergies/<int:allergy_id>/edit',
                   methods=['POST'])
@login_required
@permissions_required(ALLERGY_EDIT)
@patient_access_required
def edit_allergy(patient_id, allergy_id):
    allergy = Allergy.query.get(allergy_id)
    if allergy is None or allergy.patient_id != patient_id:
        flash('Allergy not found.', 'warning')
        return redirect(url_for('clinical.patient_360', patient_id=patient_id))
    substance = (request.form.get('substance') or '').strip()
    if substance:
        allergy.substance = substance
    allergy.reaction = request.form.get('reaction') or allergy.reaction
    allergy.severity = request.form.get('severity') or allergy.severity
    allergy.notes = request.form.get('notes') or allergy.notes
    db.session.commit()
    log_activity('EDIT_ALLERGY', 'allergy', allergy.id)
    flash('Allergy updated.', 'success')
    return redirect(url_for('clinical.patient_360', patient_id=patient_id))


# ------------------------- Structured problem list -------------------------
@clinical_bp.route('/patient/<int:patient_id>/problems/new', methods=['POST'])
@login_required
@permissions_required(PROBLEM_CREATE)
@patient_access_required
def new_problem(patient_id):
    description = (request.form.get('description') or '').strip()
    if not description:
        flash('Problem description is required.', 'warning')
        return redirect(url_for('clinical.patient_360', patient_id=patient_id))
    problem = Problem(
        patient_id=patient_id,
        icd10_code=request.form.get('icd10_code') or None,
        description=description,
        status=request.form.get('status') or 'Active',
        severity=request.form.get('severity') or 'Moderate',
        recorded_by=current_user.id,
        notes=request.form.get('notes') or None,
    )
    db.session.add(problem)
    db.session.flush()
    record_event(patient_id, 'DIAGNOSIS',
                 f'Problem list: {description}',
                 f'ICD10 {problem.icd10_code}' if problem.icd10_code else None,
                 department='Clinical')
    log_activity('CREATE_PROBLEM', 'problem', problem.id, description)
    db.session.commit()
    flash('Problem recorded.', 'success')
    return redirect(url_for('clinical.patient_360', patient_id=patient_id))


@clinical_bp.route('/patient/<int:patient_id>/problems/<int:problem_id>/edit',
                   methods=['POST'])
@login_required
@permissions_required(PROBLEM_EDIT)
@patient_access_required
def edit_problem(patient_id, problem_id):
    problem = Problem.query.get(problem_id)
    if problem is None or problem.patient_id != patient_id:
        flash('Problem not found.', 'warning')
        return redirect(url_for('clinical.patient_360', patient_id=patient_id))
    description = (request.form.get('description') or '').strip()
    if description:
        problem.description = description
    problem.icd10_code = request.form.get('icd10_code') or problem.icd10_code
    status = request.form.get('status')
    if status in ('Active', 'Inactive', 'Resolved'):
        problem.status = status
        if status == 'Resolved':
            problem.resolved_date = utcnow().date()
    problem.notes = request.form.get('notes') or problem.notes
    db.session.commit()
    log_activity('EDIT_PROBLEM', 'problem', problem.id)
    flash('Problem updated.', 'success')
    return redirect(url_for('clinical.patient_360', patient_id=patient_id))


# ------------------------- Immunizations -------------------------
@clinical_bp.route('/patient/<int:patient_id>/immunizations/new', methods=['POST'])
@login_required
@permissions_required(IMMUNIZATION_CREATE)
@patient_access_required
def new_immunization(patient_id):
    vaccine = (request.form.get('vaccine_name') or '').strip()
    if not vaccine:
        flash('Vaccine name is required.', 'warning')
        return redirect(url_for('clinical.patient_360', patient_id=patient_id))
    immunization = ImmunizationRecord(
        patient_id=patient_id, vaccine_name=vaccine,
        dose_number=int(request.form.get('dose_number') or 1),
        lot_number=request.form.get('lot_number') or None,
        site=request.form.get('site') or None,
        administered_by=current_user.id,
        notes=request.form.get('notes') or None,
    )
    next_due = request.form.get('next_due')
    if next_due:
        from datetime import datetime
        try:
            immunization.next_due = datetime.strptime(next_due, '%Y-%m-%d').date()
        except ValueError:
            immunization.next_due = None
    db.session.add(immunization)
    db.session.flush()
    record_event(patient_id, 'IMMUNIZATION', f'Immunization: {vaccine}',
                 f'Dose {immunization.dose_number}', department='Clinical')
    log_activity('CREATE_IMMUNIZATION', 'immunization', immunization.id, vaccine)
    db.session.commit()
    flash('Immunization recorded.', 'success')
    return redirect(url_for('clinical.patient_360', patient_id=patient_id))


# ------------------------- Follow-ups -------------------------
@clinical_bp.route('/patient/<int:patient_id>/followups/new', methods=['POST'])
@login_required
@permissions_required(FOLLOWUP_CREATE)
@patient_access_required
def new_followup(patient_id):
    scheduled = request.form.get('scheduled_for')
    if not scheduled:
        flash('Scheduled date/time is required.', 'warning')
        return redirect(url_for('clinical.patient_360', patient_id=patient_id))
    from datetime import datetime
    try:
        scheduled_dt = datetime.strptime(scheduled, '%Y-%m-%dT%H:%M')
    except ValueError:
        try:
            scheduled_dt = datetime.strptime(scheduled, '%Y-%m-%d')
        except ValueError:
            flash('Invalid scheduled date/time.', 'warning')
            return redirect(url_for('clinical.patient_360', patient_id=patient_id))
    follow = FollowUp(
        patient_id=patient_id,
        provider_id=_doctor().id if _doctor() else None,
        scheduled_for=scheduled_dt,
        reason=request.form.get('reason') or None,
        status='Scheduled',
        created_by=current_user.id,
    )
    db.session.add(follow)
    db.session.flush()
    record_event(patient_id, 'FOLLOW_UP', 'Follow-up scheduled',
                 follow.reason, department='Clinical')
    log_activity('CREATE_FOLLOWUP', 'follow_up', follow.id)
    db.session.commit()
    flash('Follow-up scheduled.', 'success')
    return redirect(url_for('clinical.patient_360', patient_id=patient_id))


@clinical_bp.route('/patient/<int:patient_id>/followups/<int:followup_id>/complete',
                   methods=['POST'])
@login_required
@permissions_required(FOLLOWUP_COMPLETE)
@patient_access_required
def complete_followup(patient_id, followup_id):
    follow = FollowUp.query.get(followup_id)
    if follow is None or follow.patient_id != patient_id:
        flash('Follow-up not found.', 'warning')
        return redirect(url_for('clinical.patient_360', patient_id=patient_id))
    follow.status = 'Completed'
    follow.completed_at = utcnow()
    follow.notes = request.form.get('notes') or follow.notes
    record_event(patient_id, 'FOLLOW_UP', 'Follow-up completed',
                 follow.reason, source_type='follow_up', source_id=follow.id,
                 department='Clinical')
    from app.services import tasks as task_svc
    task_svc.complete_for_resource('follow_up', follow.id, 'Follow-up completed')
    db.session.commit()
    log_activity('COMPLETE_FOLLOWUP', 'follow_up', follow.id)
    flash('Follow-up marked complete.', 'success')
    return redirect(url_for('clinical.patient_360', patient_id=patient_id))


# ============================================================================
#  REUSABLE ORDER SETS (harvest: OpenMRS/Bahmni order-set pattern, native)
# ============================================================================
ORDER_SET_CATEGORIES = ('Standard', 'Emergency', 'Pediatric', 'Chronic Screening',
                        'Pre-operative', 'Post-operative')


def _parse_order_set_items(oset):
    """Populate ``oset.items`` (not yet flushed) from order-set request lists."""
    types = request.form.getlist('item_type')
    lab_ids = request.form.getlist('lab_test_id')
    imaging_ids = request.form.getlist('imaging_type_id')
    med_ids = request.form.getlist('medication_id')
    dosages = request.form.getlist('dosage')
    freqs = request.form.getlist('frequency')
    durations = request.form.getlist('duration')
    quantities = request.form.getlist('quantity')
    instructions = request.form.getlist('instructions')
    ref_specs = request.form.getlist('referral_specialty_id')
    priorities = request.form.getlist('priority')
    item_notes = request.form.getlist('item_notes')

    for i, raw_type in enumerate(types):
        itype = (raw_type or '').upper()
        if itype not in ('LAB', 'RADIOLOGY', 'MEDICATION', 'REFERRAL'):
            continue
        item = OrderSetItem(item_type=itype,
                            priority=priorities[i] if i < len(priorities) else 'Normal')
        if itype == 'LAB':
            tid = lab_ids[i] if i < len(lab_ids) and lab_ids[i] else None
            if tid and LabTestCatalog.query.get(int(tid)):
                item.lab_test_id = int(tid)
        elif itype == 'RADIOLOGY':
            rid = imaging_ids[i] if i < len(imaging_ids) and imaging_ids[i] else None
            if rid and ImagingType.query.get(int(rid)):
                item.imaging_type_id = int(rid)
        elif itype == 'MEDICATION':
            mid = med_ids[i] if i < len(med_ids) and med_ids[i] else None
            if mid and Medication.query.get(int(mid)):
                item.medication_id = int(mid)
                item.dosage = dosages[i] if i < len(dosages) else None
                item.frequency = freqs[i] if i < len(freqs) else None
                item.duration = durations[i] if i < len(durations) else None
                item.instructions = instructions[i] if i < len(instructions) else None
                try:
                    q = int(quantities[i]) if i < len(quantities) and quantities[i] else 1
                except (TypeError, ValueError):
                    q = 1
                item.quantity = max(1, q)
        elif itype == 'REFERRAL':
            sid = ref_specs[i] if i < len(ref_specs) and ref_specs[i] else None
            if sid and Specialty.query.get(int(sid)):
                item.referral_specialty_id = int(sid)
        if (item.lab_test_id or item.imaging_type_id or item.medication_id
                or item.referral_specialty_id):
            item.notes = (item_notes[i].strip() if i < len(item_notes) else '') or None
            oset.items.append(item)


def apply_order_set(oset, patient, doctor=None):
    """Materialise an order set into real orders for a patient.

    Mirrors the individual doctor ordering flows (orders -> task queue ->
    timeline -> notifications) so departments never see a different reality.
    Runs inside the caller's transaction; the caller commits.
    """
    from app.services.clinical_orders import (create_lab_order, create_radiology_order,
                                              create_prescription, create_referral)

    counts = {'LAB': 0, 'RADIOLOGY': 0, 'MEDICATION': 0, 'REFERRAL': 0}
    origin = f'Order set "{oset.name}"'

    for item in oset.items:
        if item.item_type == 'LAB' and item.lab_test:
            create_lab_order(patient, doctor, item.lab_test_id,
                             priority=item.priority or 'Normal', notes=item.notes,
                             origin=origin)
            counts['LAB'] += 1
        elif item.item_type == 'RADIOLOGY' and item.imaging_type:
            create_radiology_order(patient, doctor, item.imaging_type_id,
                                   priority=item.priority or 'Normal',
                                   notes=item.notes, origin=origin)
            counts['RADIOLOGY'] += 1

    med_items = [it for it in oset.items
                 if it.item_type == 'MEDICATION' and it.medication]
    if med_items:
        create_prescription(patient, doctor, [{
            'medication_id': it.medication_id, 'dosage': it.dosage,
            'frequency': it.frequency, 'duration': it.duration,
            'instructions': it.instructions, 'quantity': it.quantity or 1,
        } for it in med_items], refills=0, origin=origin)
        counts['MEDICATION'] = len(med_items)

    for item in oset.items:
        if item.item_type == 'REFERRAL' and item.referral_specialty:
            urgency = {'Urgent': 'Urgent', 'Stat': 'Emergency', 'STAT': 'Emergency'}.get(
                item.priority, 'Routine')
            create_referral(patient, doctor,
                            item.notes or f'Referral via "{oset.name}"',
                            to_specialty=item.referral_specialty.name,
                            urgency=urgency, origin=origin)
            counts['REFERRAL'] += 1

    if any(counts.values()):
        log_activity('APPLY_ORDER_SET', 'order_set', oset.id,
                     f'patient={patient.id} counts={counts}')

    return counts


@clinical_bp.route('/order-sets')
@login_required
@permissions_required(ORDER_SET_VIEW)
def order_sets():
    sets = (OrderSet.query.order_by(OrderSet.name.asc()).all())
    return render_template('clinical/order_sets.html', title='Order Sets',
                           order_sets=sets, categories=ORDER_SET_CATEGORIES)


@clinical_bp.route('/order-sets/new', methods=['GET', 'POST'])
@login_required
@permissions_required(ORDER_SET_CREATE)
def order_set_new():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        existing = OrderSet.query.filter_by(name=name).first()
        if not name:
            flash('Order set name is required.', 'warning')
        elif existing:
            flash('An order set with that name already exists.', 'warning')
        else:
            oset = OrderSet(
                name=name,
                description=(request.form.get('description') or '').strip() or None,
                category=request.form.get('category') or None,
                specialty_id=(int(request.form['specialty_id'])
                              if request.form.get('specialty_id') else None),
                department_id=(int(request.form['department_id'])
                               if request.form.get('department_id') else None),
                is_published=request.form.get('is_published') == 'on',
                is_active=False,
                created_by=current_user.id,
            )
            _parse_order_set_items(oset)
            if not oset.items:
                flash('Add at least one valid item to the order set.', 'danger')
            else:
                db.session.add(oset)
                db.session.flush()
                log_activity('CREATE_ORDER_SET', 'order_set', oset.id, name)
                db.session.commit()
                flash('Order set created.', 'success')
                return redirect(url_for('clinical.order_set_detail', order_set_id=oset.id))
    specialties = Specialty.query.order_by(Specialty.name).all()
    departments = Department.query.order_by(Department.name).all()
    tests = LabTestCatalog.query.filter_by(is_active=True) \
        .order_by(LabTestCatalog.test_name).all()
    imaging = ImagingType.query.order_by(ImagingType.name).all()
    meds = Medication.query.filter_by(is_active=True) \
        .order_by(Medication.generic_name).all()
    return render_template('clinical/order_set_form.html', title='New Order Set',
                           oset=None, specialties=specialties,
                           departments=departments, tests=tests, imaging=imaging,
                           meds=meds, categories=ORDER_SET_CATEGORIES)


@clinical_bp.route('/order-sets/<int:order_set_id>')
@login_required
@permissions_required(ORDER_SET_VIEW)
def order_set_detail(order_set_id):
    oset = OrderSet.query.get_or_404(order_set_id)
    return render_template('clinical/order_set_detail.html', title=oset.name,
                           oset=oset)


@clinical_bp.route('/order-sets/<int:order_set_id>/toggle', methods=['POST'])
@login_required
@permissions_required(ORDER_SET_EDIT)
def order_set_toggle(order_set_id):
    oset = OrderSet.query.get_or_404(order_set_id)
    oset.is_active = not oset.is_active
    log_activity('TOGGLE_ORDER_SET', 'order_set', oset.id,
                 f'active={oset.is_active}')
    db.session.commit()
    flash('Order set updated.', 'success')
    return redirect(url_for('clinical.order_set_detail', order_set_id=oset.id))


@clinical_bp.route('/order-sets/<int:order_set_id>/apply', methods=['GET'])
@login_required
@permissions_required(ORDER_SET_CREATE)
def order_set_apply_picker(order_set_id):
    oset = OrderSet.query.get_or_404(order_set_id)
    if not oset.is_active:
        flash('This order set is inactive.', 'warning')
        return redirect(url_for('clinical.order_set_detail', order_set_id=oset.id))
    pids = accessible_patient_ids(current_user)
    patients = (Patient.query.join(User, Patient.user_id == User.id)
                .filter(Patient.id.in_(pids or [-1]))
                .order_by(User.full_name).all())
    return render_template('clinical/order_set_apply.html', title=f'Apply {oset.name}',
                           oset=oset, patients=patients)


@clinical_bp.route('/order-sets/<int:order_set_id>/apply/<int:patient_id>',
                   methods=['POST'])
@login_required
@permissions_required(ORDER_SET_CREATE)
@patient_access_required
def order_set_apply(order_set_id, patient_id):
    oset = OrderSet.query.get_or_404(order_set_id)
    patient = Patient.query.get_or_404(patient_id)
    doctor = _doctor()
    if not oset.is_active:
        flash('This order set is inactive.', 'warning')
        return redirect(url_for('clinical.order_set_detail', order_set_id=oset.id))
    if not oset.items:
        flash('This order set has no items.', 'warning')
        return redirect(url_for('clinical.order_set_detail', order_set_id=oset.id))
    counts = apply_order_set(oset, patient, doctor)
    db.session.commit()
    flash(f'Order set "{oset.name}" applied: {counts["LAB"]} lab, '
          f'{counts["RADIOLOGY"]} imaging, {counts["MEDICATION"]} medication(s), '
          f'{counts["REFERRAL"]} referral(s).', 'success')
    return redirect(url_for('clinical.patient_360', patient_id=patient.id))


# ============================================================================
#  CLINICAL TEMPLATES (harvest: OpenEMR template-driven forms, native)
# ============================================================================
TEMPLATE_TYPES = ('SOAP', 'CONSULT', 'DISCHARGE', 'PROCEDURE', 'NURSING')
DEFAULT_SOAP_SECTIONS = [
    {'key': 's', 'label': 'Subjective', 'placeholder': 'Patient complaints, history...'},
    {'key': 'o', 'label': 'Objective', 'placeholder': 'Examination findings, vitals, labs...'},
    {'key': 'a', 'label': 'Assessment', 'placeholder': 'Diagnosis / assessment...'},
    {'key': 'p', 'label': 'Plan', 'placeholder': 'Treatment plan, follow-up, orders...'},
]


def _parse_template_sections():
    keys = request.form.getlist('section_key')
    labels = request.form.getlist('section_label')
    placeholders = request.form.getlist('section_placeholder')
    types = request.form.getlist('section_type')
    options_list = request.form.getlist('section_options')
    rows = []
    for i, key in enumerate(keys):
        key = (key or '').strip()
        if not key:
            continue
        label = (labels[i] if i < len(labels) else '') or key
        rows.append({
            'key': key,
            'label': label.strip(),
            'placeholder': (placeholders[i] if i < len(placeholders) else '').strip(),
            'type': (types[i] if i < len(types) else '') or 'textarea',
            'options': (options_list[i] if i < len(options_list) else '') or '',
        })
    from app.services.templates import normalize_sections, ALLOWED_TYPES
    try:
        out = normalize_sections(rows)
    except ValueError as exc:
        flash(str(exc), 'warning')
        out = DEFAULT_SOAP_SECTIONS
    return out


@clinical_bp.route('/templates')
@login_required
@permissions_required(TEMPLATE_VIEW)
def templates():
    items = (ClinicianTemplate.query
             .order_by(ClinicianTemplate.title.asc()).all())
    return render_template('clinical/templates.html', title='Clinical Templates',
                           templates=items, template_types=TEMPLATE_TYPES)


@clinical_bp.route('/templates/new', methods=['GET', 'POST'])
@login_required
@permissions_required(TEMPLATE_CREATE)
def template_new():
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        existing = ClinicianTemplate.query.filter_by(title=title).first()
        if not title:
            flash('Template title is required.', 'warning')
        elif existing:
            flash('A template with that title already exists.', 'warning')
        else:
            tpl = ClinicianTemplate(
                title=title,
                template_type=request.form.get('template_type') or 'SOAP',
                description=(request.form.get('description') or '').strip() or None,
                specialty_id=(int(request.form['specialty_id'])
                              if request.form.get('specialty_id') else None),
                sections=json.dumps(_parse_template_sections(), ensure_ascii=False),
                allowed_roles=request.form.get('allowed_roles') or None,
                created_by=current_user.id,
            )
            db.session.add(tpl)
            db.session.flush()
            log_activity('CREATE_TEMPLATE', 'clinician_template', tpl.id, title)
            db.session.commit()
            flash('Template created.', 'success')
            return redirect(url_for('clinical.templates'))
    specialties = Specialty.query.order_by(Specialty.name).all()
    return render_template('clinical/template_form.html', title='New Template',
                           tpl=None, specialties=specialties,
                           template_types=TEMPLATE_TYPES)


@clinical_bp.route('/templates/<int:template_id>/edit', methods=['GET', 'POST'])
@login_required
@permissions_required(TEMPLATE_EDIT)
def template_edit(template_id):
    tpl = ClinicianTemplate.query.get_or_404(template_id)
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        conflict = (ClinicianTemplate.query
                    .filter(ClinicianTemplate.title == title,
                            ClinicianTemplate.id != tpl.id).first())
        if not title:
            flash('Template title is required.', 'warning')
        elif conflict:
            flash('A template with that title already exists.', 'warning')
        else:
            tpl.title = title
            tpl.template_type = request.form.get('template_type') or tpl.template_type
            tpl.description = (request.form.get('description') or '').strip() or None
            tpl.specialty_id = (int(request.form['specialty_id'])
                                if request.form.get('specialty_id') else None)
            tpl.sections = json.dumps(_parse_template_sections(), ensure_ascii=False)
            tpl.allowed_roles = request.form.get('allowed_roles') or None
            log_activity('EDIT_TEMPLATE', 'clinician_template', tpl.id, title)
            db.session.commit()
            flash('Template updated.', 'success')
            return redirect(url_for('clinical.templates'))
    specialties = Specialty.query.order_by(Specialty.name).all()
    return render_template('clinical/template_form.html',
                           title=f'Edit {tpl.title}', tpl=tpl,
                           specialties=specialties, template_types=TEMPLATE_TYPES)


@clinical_bp.route('/templates/<int:template_id>/toggle', methods=['POST'])
@login_required
@permissions_required(TEMPLATE_EDIT)
def template_toggle(template_id):
    tpl = ClinicianTemplate.query.get_or_404(template_id)
    tpl.is_active = not tpl.is_active
    log_activity('TOGGLE_TEMPLATE', 'clinician_template', tpl.id,
                 f'active={tpl.is_active}')
    db.session.commit()
    flash('Template updated.', 'success')
    return redirect(url_for('clinical.templates'))


# ============================================================================
#  CLINICAL INBOX & RESULT ACKNOWLEDGEMENT (harvest: OpenEMR results review)
# ============================================================================
@clinical_bp.route('/inbox')
@login_required
@permissions_required(INBOX_VIEW)
def inbox():
    from app.services.reminders import open_reminders_for_patients, scan_due_reminders
    pids = accessible_patient_ids(current_user)
    pid_filter = pids or [-1]

    from sqlalchemy.orm import joinedload

    def _acked(kind):
        return {rid for (rid,) in db.session.query(ResultAcknowledgement.result_id)
                .filter_by(result_type=kind, ack_by=current_user.id).all()}

    # Verified/signed results not yet acknowledged by this clinician. Joined
    # and eager-loaded in one round-trip each (previously an N+1 per order).
    acked_lab = _acked('LAB')
    lab_rows = [o for o in (LabOrder.query
                            .join(LabResult, LabResult.order_id == LabOrder.id)
                            .filter(LabOrder.patient_id.in_(pid_filter),
                                    LabResult.status.in_(['Verified', 'Locked']))
                            .options(joinedload(LabOrder.result),
                                     joinedload(LabOrder.test),
                                     joinedload(LabOrder.patient).joinedload(Patient.user))
                            .order_by(LabOrder.order_date.desc()).limit(120).all())
                if o.result and o.result.id not in acked_lab]

    acked_rad = _acked('RADIOLOGY')
    rad_rows = [o for o in (RadiologyOrder.query
                            .join(RadiologyReport, RadiologyReport.order_id == RadiologyOrder.id)
                            .filter(RadiologyOrder.patient_id.in_(pid_filter),
                                    RadiologyReport.status.in_(['Signed', 'Locked']))
                            .options(joinedload(RadiologyOrder.report),
                                     joinedload(RadiologyOrder.imaging_type),
                                     joinedload(RadiologyOrder.patient).joinedload(Patient.user))
                            .order_by(RadiologyOrder.order_date.desc()).limit(120).all())
                if o.report and o.report.id not in acked_rad]

    open_alerts = (ClinicalAlert.query
                   .filter(ClinicalAlert.patient_id.in_(pid_filter),
                           ClinicalAlert.status == 'OPEN')
                   .order_by(ClinicalAlert.created_at.desc()).limit(30).all())
    pending_refs = (Referral.query
                    .filter(Referral.patient_id.in_(pid_filter),
                            Referral.status.in_(['Pending', 'SENT', 'IN_REVIEW']))
                    .order_by(Referral.created_at.desc()).limit(30).all())
    interventions = (PharmacyIntervention.query
                     .filter(PharmacyIntervention.patient_id.in_(pid_filter),
                             PharmacyIntervention.status == 'OPEN')
                     .order_by(PharmacyIntervention.created_at.desc()).limit(20).all())
    drafts = (MedicalRecord.query
              .filter(MedicalRecord.patient_id.in_(pid_filter),
                      MedicalRecord.status == 'Draft')
              .order_by(MedicalRecord.visit_date.desc()).limit(20).all())
    my_tasks = (Task.query
                .filter(Task.assigned_to == current_user.id,
                        Task.status.in_(['NEW', 'ASSIGNED', 'IN_PROGRESS']),
                        Task.patient_id.in_(pid_filter))
                .order_by(Task.created_at.desc()).limit(20).all())

    scan_due_reminders(current_user.id)
    db.session.commit()
    reminders = open_reminders_for_patients(pids, limit=30)

    counts = {
        'lab': len(lab_rows), 'radiology': len(rad_rows), 'alerts': len(open_alerts),
        'referrals': len(pending_refs), 'interventions': len(interventions),
        'drafts': len(drafts), 'tasks': len(my_tasks), 'reminders': len(reminders),
        'critical_labs': sum(1 for o in lab_rows if o.result.is_critical),
    }
    return render_template('clinical/inbox.html', title='Clinical Inbox',
                           lab_rows=lab_rows, rad_rows=rad_rows,
                           open_alerts=open_alerts, pending_refs=pending_refs,
                           interventions=interventions, drafts=drafts,
                           my_tasks=my_tasks, reminders=reminders, counts=counts,
                           today=utcnow().date())


@clinical_bp.route('/inbox/ack', methods=['POST'])
@login_required
@permissions_required(RESULT_ACK)
def inbox_ack():
    result_type = request.form.get('result_type')
    note = request.form.get('note') or None
    try:
        result_id = int(request.form.get('result_id'))
        patient_id = int(request.form.get('patient_id'))
    except (TypeError, ValueError):
        flash('Invalid acknowledgement target.', 'warning')
        return redirect(url_for('clinical.inbox'))

    patient = Patient.query.get(patient_id)
    if patient is None:
        flash('Patient not found.', 'warning')
        return redirect(url_for('clinical.inbox'))
    require_patient_access(patient)

    if result_type == 'LAB':
        obj = LabResult.query.get(result_id)
        if obj is None or obj.order.patient_id != patient_id:
            flash('Result not found.', 'warning')
            return redirect(url_for('clinical.inbox'))
        kind = 'CRITICAL' if obj.is_critical else 'REVIEW'
    elif result_type == 'RADIOLOGY':
        obj = RadiologyReport.query.get(result_id)
        if obj is None or obj.order.patient_id != patient_id:
            flash('Report not found.', 'warning')
            return redirect(url_for('clinical.inbox'))
        kind = 'REVIEW'
    else:
        flash('Unknown result type.', 'warning')
        return redirect(url_for('clinical.inbox'))

    existing = (ResultAcknowledgement.query
                .filter_by(result_type=result_type, result_id=result_id,
                           ack_by=current_user.id).first())
    if existing:
        existing.kind = kind
        existing.note = note or existing.note
        existing.ack_at = utcnow()
    else:
        db.session.add(ResultAcknowledgement(
            patient_id=patient_id, result_type=result_type, result_id=result_id,
            kind=kind, ack_by=current_user.id, note=note))
    log_activity('ACK_RESULT', result_type.lower(), result_id,
                 f'patient={patient_id} kind={kind}')
    db.session.commit()
    flash('Result acknowledged.', 'success')
    return redirect(url_for('clinical.inbox'))


# ============================================================================
#  RECALL BOARD (harvest: OpenEMR recall board) — upcoming/due/overdue at a glance
# ============================================================================
@clinical_bp.route('/recall-board')
@login_required
@permissions_required(REMINDER_VIEW)
def recall_board():
    from app.services.reminders import open_reminders_for_patients, scan_due_reminders
    pids = accessible_patient_ids(current_user)
    scan_due_reminders(current_user.id)
    db.session.commit()
    reminders = open_reminders_for_patients(pids)
    followups = (FollowUp.query
                 .filter(FollowUp.patient_id.in_(pids),
                         FollowUp.status == 'Scheduled')
                 .order_by(FollowUp.scheduled_for.asc()).all()) if pids else []
    today = utcnow().date()

    def _bucket(items, when):
        overdue, due_today, upcoming = [], [], []
        for it in items:
            d = None
            if hasattr(it, 'due_date'):
                d = it.due_date
            elif hasattr(it, 'scheduled_for'):
                d = it.scheduled_for.date() if it.scheduled_for else None
            if d is None:
                upcoming.append(it)
            elif d < today:
                overdue.append(it)
            elif d == today:
                due_today.append(it)
            else:
                upcoming.append(it)
        return overdue, due_today, upcoming

    r_over, r_today, r_up = _bucket(reminders, today)
    f_over, f_today, f_up = _bucket(followups, today)
    return render_template('clinical/recall_board.html', title='Recall Board',
                           reminders=reminders, r_over=r_over, r_today=r_today,
                           r_up=r_up, f_over=f_over, f_today=f_today, f_up=f_up,
                           today=today)


# ============================================================================
#  CLINICAL REMINDERS (recall engine — harvest: OpenEMR recall board)
# ============================================================================
@clinical_bp.route('/reminders')
@login_required
@permissions_required(REMINDER_VIEW)
def reminders():
    from app.services.reminders import open_reminders_for_patients, scan_due_reminders
    pids = accessible_patient_ids(current_user)
    scan_due_reminders(current_user.id)
    db.session.commit()
    items = open_reminders_for_patients(pids)
    today = utcnow().date()
    overdue = [r for r in items if r.due_date and r.due_date < today]
    return render_template('clinical/reminders.html', title='Clinical Reminders',
                           reminders=items, overdue=overdue, today=today)


@clinical_bp.route('/reminders/<int:reminder_id>/done', methods=['POST'])
@login_required
@permissions_required(REMINDER_ACK)
def reminder_done(reminder_id):
    reminder = ClinicalReminder.query.get_or_404(reminder_id)
    require_patient_access(reminder.patient)
    reminder.status = 'DONE'
    reminder.completed_at = utcnow()
    reminder.completed_by = current_user.id
    log_activity('COMPLETE_REMINDER', 'clinical_reminder', reminder.id)
    db.session.commit()
    flash('Reminder completed.', 'success')
    return redirect(url_for('clinical.reminders'))


# ============================================================================
#  CLINICAL SUMMARY (verified data summary — harvest: OpenMRS chart review)
# ============================================================================
@clinical_bp.route('/summary/<int:patient_id>')
@login_required
@permissions_required(CLINICAL_SUMMARY_VIEW)
@patient_access_required
def patient_summary(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    allergies = (Allergy.query
                 .filter_by(patient_id=patient_id, status='Active')
                 .order_by(Allergy.created_at.desc()).all())
    problems = (Problem.query
                .filter_by(patient_id=patient_id, status='Active')
                .order_by(Problem.created_at.desc()).all())
    active_rxs = (Prescription.query
                  .filter_by(patient_id=patient_id, status='Active')
                  .order_by(Prescription.prescribed_date.desc()).all())
    active_meds = []
    seen_meds = set()
    for rx in active_rxs:
        for item in rx.items:
            if item.status == 'Cancelled' or item.medication_id in seen_meds:
                continue
            seen_meds.add(item.medication_id)
            active_meds.append(item)
    latest_vitals = (VitalSign.query.filter_by(patient_id=patient_id)
                     .order_by(VitalSign.recorded_at.desc()).first())
    lab_results = (LabResult.query
                   .join(LabOrder, LabResult.order_id == LabOrder.id)
                   .filter(LabOrder.patient_id == patient_id,
                           LabResult.status.in_(['Verified', 'Locked']))
                   .order_by(LabResult.result_date.desc()).limit(20).all())
    radiology = (RadiologyReport.query
                 .join(RadiologyOrder, RadiologyReport.order_id == RadiologyOrder.id)
                 .filter(RadiologyOrder.patient_id == patient_id,
                         RadiologyReport.status.in_(['Signed', 'Locked']))
                 .order_by(RadiologyReport.report_date.desc()).limit(10).all())
    admissions = (Admission.query.filter_by(patient_id=patient_id)
                  .order_by(Admission.admitted_at.desc()).limit(10).all())
    immunizations = (ImmunizationRecord.query
                     .filter_by(patient_id=patient_id)
                     .order_by(ImmunizationRecord.administered_at.desc()).all())
    medical_records = (MedicalRecord.query.filter_by(patient_id=patient_id)
                       .order_by(MedicalRecord.visit_date.desc()).all())
    open_alerts = alert_svc.open_alerts_for_patient(patient_id)
    care_members = (CareTeamMember.query
                    .join(CareTeam, CareTeamMember.team_id == CareTeam.id)
                    .filter(CareTeam.patient_id == patient_id)
                    .order_by(CareTeamMember.role)
                    .all())
    return render_template(
        'clinical/summary.html', title='Clinical Summary', patient=patient,
        allergies=allergies, problems=problems, active_rxs=active_rxs,
        active_meds=active_meds, latest_vitals=latest_vitals,
        lab_results=lab_results, radiology=radiology, admissions=admissions,
        immunizations=immunizations, medical_records=medical_records,
        open_alerts=open_alerts, care_members=care_members, today=utcnow().date())