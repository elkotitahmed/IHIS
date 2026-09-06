from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, abort, current_app
from flask_login import login_required, current_user
from datetime import datetime
import os
from app import db
from app.models import (
    Patient, Doctor, Specialty, Appointment, MedicalRecord, Prescription,
    LabOrder, RadiologyOrder, Notification, Message, Diagnosis, VitalSign,
    PatientDocument, Bill, PatientImagingSafetyProfile, MRIImplantRegistry,
    ImagingDoseRecord, ContrastAdministration,
)
from app.routes.decorators import roles_required, log_activity, save_upload
from app.utils import has_appointment_conflict, utcnow

patient_bp = Blueprint('patient', __name__)
ALLOWED = ['Patient', 'Doctor', 'Admin', 'SuperAdmin']


def _current_patient():
    """The patient record behind the portal.

    Patients see their own record. Admin/SuperAdmin (including a SuperAdmin
    in *Role Preview* as Patient) walk the portal on behalf of a chosen
    patient - the ``preview_patient_id`` session key, defaulting to the first
    registered patient - so the portal can be demonstrated without a
    patient login. Every such access is audited on the download routes and
    stays read-mostly (profile edits are blocked for supervisors)."""
    own = Patient.query.filter_by(user_id=current_user.id).first()
    if own is not None:
        return own
    if current_user.has_any_role('Admin', 'SuperAdmin'):
        from flask import session
        pid = session.get('preview_patient_id')
        patient = db.session.get(Patient, pid) if pid else None
        if patient is None:
            patient = Patient.query.order_by(Patient.id.asc()).first()
        return patient
    return None


def _is_own_record(patient):
    return patient is not None and patient.user_id == current_user.id


def _require_patient():
    """Resolve the portal patient or redirect with a clear message."""
    patient = _current_patient()
    if patient is None:
        if current_user.user_type == 'patient':
            flash('Please complete your patient profile first.', 'warning')
            return None, redirect(url_for('patient.profile'))
        flash('No patient record is available for the portal preview.', 'warning')
        return None, redirect(url_for('main.dashboard'))
    return patient, None


@patient_bp.route('/preview/<int:patient_id>')
@login_required
@roles_required('Admin', 'SuperAdmin')
def preview_as(patient_id):
    """Choose which patient's portal a supervisor is walking through."""
    from flask import session
    patient = Patient.query.get_or_404(patient_id)
    session['preview_patient_id'] = patient.id
    log_activity('PATIENT_PORTAL_PREVIEW', 'patient', patient.id,
                 f'Supervisor {current_user.id} viewing portal as patient')
    db.session.commit()
    return redirect(url_for('patient.dashboard'))


@patient_bp.route('/dashboard')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def dashboard():
    patient, redirect_resp = _require_patient()
    if redirect_resp:
        return redirect_resp
    upcoming = Appointment.query.filter(
        Appointment.patient_id == patient.id,
        Appointment.status.in_(('Scheduled', 'Confirmed', 'CheckedIn'))
    ).order_by(Appointment.scheduled_at).limit(5).all()
    recent_labs = LabOrder.query.filter_by(patient_id=patient.id).order_by(
        LabOrder.order_date.desc()).limit(5).all()
    prescriptions = (Prescription.query.filter_by(patient_id=patient.id)
                     .order_by(Prescription.prescribed_date.desc()).limit(5).all())
    notify_uid = patient.user_id if _is_own_record(patient) else patient.user_id
    notifications = Notification.query.filter_by(
        user_id=notify_uid).order_by(Notification.created_at.desc()).limit(5).all()
    open_bills = [b for b in Bill.query.filter_by(patient_id=patient.id).all()
                  if b.status in ('Unpaid', 'PartiallyPaid')]
    balance_due = sum(b.balance() for b in open_bills)
    other_patients = []
    if not _is_own_record(patient) and current_user.has_any_role('Admin', 'SuperAdmin'):
        other_patients = Patient.query.order_by(Patient.id.asc()).limit(50).all()
    from app.models import FollowUp, Problem
    follow_ups = (FollowUp.query.filter(FollowUp.patient_id == patient.id,
                                        FollowUp.status.in_(('Scheduled', 'Pending', 'Overdue')))
                  .order_by(FollowUp.scheduled_for.asc()).limit(3).all())
    problems = (Problem.query.filter(Problem.patient_id == patient.id, Problem.status == 'Active')
                .order_by(Problem.created_at.desc()).limit(5).all())
    active_items = []
    for rx in prescriptions:
        if rx.status == 'Active':
            active_items += [it for it in rx.items if it.status != 'Cancelled']
    return render_template('patient/dashboard.html', title='Patient Dashboard',
                           patient=patient, upcoming=upcoming, recent_labs=recent_labs,
                           prescriptions=prescriptions, notifications=notifications,
                           open_bills=open_bills, balance_due=balance_due,
                           follow_ups=follow_ups, problems=problems, active_items=active_items[:6],
                           is_own=_is_own_record(patient), other_patients=other_patients)


@patient_bp.route('/health-summary')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def health_summary():
    """MY HEALTH SUMMARY: the patient's own verified data, plain and simple.
    Clinician-only content (drafts, internal notes, unreleased results) is
    never shown; AI explanations are explicit and optional."""
    patient, redirect_resp = _require_patient()
    if redirect_resp:
        return redirect_resp
    from app.models import Allergy, ClinicalAlert, FollowUp, Problem
    from app.services.ai.patient_education import find_terms
    problems = (Problem.query.filter(Problem.patient_id == patient.id, Problem.status == 'Active')
                .order_by(Problem.created_at.desc()).limit(10).all())
    allergies = Allergy.query.filter_by(patient_id=patient.id).limit(10).all()
    items = []
    for rx in (Prescription.query.filter_by(patient_id=patient.id, status='Active')
               .order_by(Prescription.prescribed_date.desc()).limit(10).all()):
        items += [it for it in rx.items if it.status != 'Cancelled']
    released = [o for o in LabOrder.query.filter_by(patient_id=patient.id)
                .order_by(LabOrder.order_date.desc()).limit(30).all()
                if o.result and o.result.status in ('Verified', 'Locked', 'Finalized')][:8]
    upcoming = (Appointment.query.filter(Appointment.patient_id == patient.id,
                                         Appointment.status.in_(('Scheduled', 'Confirmed', 'CheckedIn')))
                .order_by(Appointment.scheduled_at).limit(5).all())
    follow_ups = (FollowUp.query.filter(FollowUp.patient_id == patient.id,
                                        FollowUp.status.in_(('Scheduled', 'Pending', 'Overdue')))
                  .order_by(FollowUp.scheduled_for.asc()).limit(5).all())
    # Patient-relevant alerts only: preventive / follow-up / vaccine reminders — never
    # internal clinical-safety alerts meant for staff.
    patient_alerts = (ClinicalAlert.query
                      .filter(ClinicalAlert.patient_id == patient.id,
                              ClinicalAlert.status.in_(('OPEN', 'ACKNOWLEDGED', 'IN_PROGRESS')),
                              ClinicalAlert.alert_type.in_(('VACCINE_DUE', 'UPCOMING_FOLLOWUP',
                                                            'OVERDUE_FOLLOWUP', 'MISSED_APPOINTMENT')))
                      .order_by(ClinicalAlert.created_at.desc()).limit(5).all())
    last_record = (MedicalRecord.query.filter_by(patient_id=patient.id, status='Signed')
                   .order_by(MedicalRecord.visit_date.desc()).first())
    instructions = last_record.treatment_plan if last_record and last_record.treatment_plan else None
    terms = find_terms(' '.join([p.description or '' for p in problems] + [instructions or '']))
    return render_template('patient/health_summary.html', title='My Health Summary', patient=patient,
                           problems=problems, allergies=allergies, items=items, released=released,
                           upcoming=upcoming, follow_ups=follow_ups, patient_alerts=patient_alerts,
                           instructions=instructions, terms=terms, is_own=_is_own_record(patient))


@patient_bp.route('/profile', methods=['GET', 'POST'])
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def profile():
    patient, redirect_resp = _require_patient()
    if redirect_resp:
        return redirect_resp
    if request.method == 'POST':
        if not _is_own_record(patient):
            flash('Profile changes must be made by the patient (or via the reception desk).', 'warning')
            return redirect(url_for('patient.profile'))
        user = current_user
        user.phone = request.form.get('phone') or user.phone
        patient.phone = request.form.get('phone') or patient.phone
        user.full_name = request.form.get('full_name') or user.full_name
        patient.date_of_birth = datetime.strptime(request.form['date_of_birth'], '%Y-%m-%d') \
            if request.form.get('date_of_birth') else patient.date_of_birth
        patient.gender = request.form.get('gender') or patient.gender
        patient.address = request.form.get('address') or patient.address
        patient.blood_type = request.form.get('blood_type') or patient.blood_type
        patient.allergies = request.form.get('allergies') or patient.allergies
        patient.chronic_diseases = request.form.get('chronic_diseases') or patient.chronic_diseases
        patient.emergency_contact = request.form.get('emergency_contact') or patient.emergency_contact
        patient.vaccination_records = request.form.get('vaccination_records') or patient.vaccination_records
        db.session.commit()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('patient.profile'))
    return render_template('patient/profile.html', title='My Profile', patient=patient,
                           is_own=_is_own_record(patient))


@patient_bp.route('/medical-history')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def medical_history():
    patient, redirect_resp = _require_patient()
    if redirect_resp:
        return redirect_resp
    records = MedicalRecord.query.filter_by(patient_id=patient.id).order_by(
        MedicalRecord.visit_date.desc()).all()
    diagnoses = Diagnosis.query.filter_by(patient_id=patient.id).order_by(
        Diagnosis.date_diagnosed.desc()).all()
    vitals = VitalSign.query.filter_by(patient_id=patient.id).order_by(
        VitalSign.recorded_at.desc()).limit(20).all()
    return render_template('patient/medical_history.html', title='Medical History',
                           patient=patient, records=records, diagnoses=diagnoses, vitals=vitals)


@patient_bp.route('/appointments')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def appointments():
    patient, redirect_resp = _require_patient()
    if redirect_resp:
        return redirect_resp
    items = Appointment.query.filter_by(patient_id=patient.id).order_by(
        Appointment.scheduled_at.desc()).all()
    return render_template('patient/appointments.html', title='My Appointments', items=items)


@patient_bp.route('/appointments/book', methods=['GET', 'POST'])
@login_required
@roles_required('Patient', 'Receptionist', 'Admin', 'SuperAdmin')
def book_appointment():
    if request.method == 'POST':
        doctor = db.session.get(Doctor, request.form.get('doctor_id', type=int) or -1)
        if not doctor:
            flash('Please select a valid physician.', 'danger')
            return redirect(url_for('patient.book_appointment'))
        patient = Patient.query.filter_by(user_id=current_user.id).first()
        if not patient and current_user.has_any_role('Admin', 'SuperAdmin', 'Receptionist'):
            patient = (db.session.get(Patient, request.form.get('patient_id', type=int) or -1)
                       or _current_patient())
        if not patient:
            flash('A valid patient is required to book an appointment.', 'danger')
            return redirect(url_for('patient.book_appointment'))
        try:
            scheduled_at = datetime.strptime(
                f"{request.form.get('date')} {request.form.get('time')}", '%Y-%m-%d %H:%M')
        except (ValueError, TypeError):
            flash('Please provide a valid appointment date and time.', 'danger')
            return redirect(url_for('patient.book_appointment'))
        duration = int(request.form.get('duration_minutes') or 30)
        if has_appointment_conflict(doctor.id, scheduled_at, duration):
            flash('This physician already has an appointment at that time. Please choose another slot.', 'warning')
            return redirect(url_for('patient.book_appointment'))
        if scheduled_at < utcnow():
            flash('Please choose a future date and time.', 'warning')
            return redirect(url_for('patient.book_appointment'))
        appt = Appointment(
            patient_id=patient.id,
            doctor_id=doctor.id,
            scheduled_at=scheduled_at,
            duration_minutes=duration,
            reason=request.form.get('reason'),
            priority=request.form.get('priority', 'Normal'),
            created_by=current_user.id,
        )
        db.session.add(appt)
        db.session.flush()
        log_activity('BOOK_APPOINTMENT', 'appointment', appt.id, f'doctor={doctor.id}')
        from app.services.timeline import record_event
        from app.services.notifications import notify
        record_event(patient.id, 'APPOINTMENT', 'Appointment booked',
                     f'With Dr. {doctor.user.full_name if doctor.user else "physician"} on '
                     f'{scheduled_at.strftime("%d %b %Y %H:%M")}',
                     source_type='appointment', source_id=appt.id,
                     department='Patient Portal')
        if doctor.user_id:
            notify(doctor.user_id, 'New appointment booked',
                   f'{patient.user.full_name if patient.user else "A patient"} booked '
                   f'{scheduled_at.strftime("%d %b %Y %H:%M")}.',
                   entity_type='appointment', entity_id=appt.id)
        db.session.commit()
        flash('Appointment booked successfully.', 'success')
        return redirect(url_for('patient.appointments'))

    from app.models import User as UserModel
    doctors = Doctor.query.join(UserModel, Doctor.user_id == UserModel.id).order_by(UserModel.full_name).all()
    return render_template('patient/book_appointment.html', title='Book Appointment', doctors=doctors)


@patient_bp.route('/prescriptions')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def prescriptions():
    patient, redirect_resp = _require_patient()
    if redirect_resp:
        return redirect_resp
    items = Prescription.query.filter_by(patient_id=patient.id).order_by(
        Prescription.prescribed_date.desc()).all()
    return render_template('patient/prescriptions.html', title='My Prescriptions', items=items)


@patient_bp.route('/lab-results')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def lab_results():
    patient, redirect_resp = _require_patient()
    if redirect_resp:
        return redirect_resp
    items = LabOrder.query.filter_by(patient_id=patient.id).order_by(
        LabOrder.order_date.desc()).all()
    return render_template('patient/lab_results.html', title='Lab Results', items=items)


@patient_bp.route('/radiology-reports')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def radiology_reports():
    patient, redirect_resp = _require_patient()
    if redirect_resp:
        return redirect_resp
    items = RadiologyOrder.query.filter_by(patient_id=patient.id).order_by(
        RadiologyOrder.order_date.desc()).all()
    return render_template('patient/radiology_reports.html', title='Radiology Reports', items=items)


@patient_bp.route('/my-radiology')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def my_radiology():
    """Patient portal — My Radiology: safety profile, dose summary, order history."""
    from app.services.radiology.dose_service import RadiationDoseService
    from app.services.radiology.safety_service import RadiologySafetyService
    from app.models import (
        PatientImagingSafetyProfile, MRIImplantRegistry,
        ImagingDoseRecord, ContrastAdministration,
    )
    patient, redirect_resp = _require_patient()
    if redirect_resp:
        return redirect_resp
    safety_svc = RadiologySafetyService()
    dose_svc = RadiationDoseService()
    profile = PatientImagingSafetyProfile.query.filter_by(
        patient_id=patient.id).first()
    implants = MRIImplantRegistry.query.filter_by(
        patient_id=patient.id, is_active=True).all()
    ct_eval = safety_svc.evaluate_ct_safety(patient.id)
    mri_eval = safety_svc.evaluate_mri_safety(patient.id)
    annual = dose_svc.get_patient_annual_summary(patient.id)
    orders = RadiologyOrder.query.filter_by(patient_id=patient.id).order_by(
        RadiologyOrder.order_date.desc()).all()
    contrast_history = ContrastAdministration.query.filter_by(
        patient_id=patient.id).order_by(
        ContrastAdministration.administration_time.desc()).limit(5).all()
    return render_template('patient/my_radiology.html',
                           title='My Radiology',
                           patient=patient, profile=profile,
                           implants=implants, ct_eval=ct_eval,
                           mri_eval=mri_eval, annual=annual,
                           orders=orders, contrast_history=contrast_history)


@patient_bp.route('/bills')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def bills():
    patient, redirect_resp = _require_patient()
    if redirect_resp:
        return redirect_resp
    items = Bill.query.filter_by(patient_id=patient.id).order_by(
        Bill.issued_at.desc()).all()
    total_billed = sum(b.total() for b in items)
    total_balance = sum(b.balance() for b in items)
    total_paid = sum(b.paid_amount() for b in items)
    return render_template('patient/bills.html', title='My Bills & Receipts',
                           items=items, total_billed=total_billed,
                           total_balance=total_balance, total_paid=total_paid)


@patient_bp.route('/documents', methods=['GET', 'POST'])
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def documents():
    patient, redirect_resp = _require_patient()
    if redirect_resp:
        return redirect_resp
    if request.method == 'POST':
        if not _is_own_record(patient):
            flash('Only the patient can upload to their own document folder.', 'warning')
            return redirect(url_for('patient.documents'))
        f = request.files.get('document')
        url = save_upload(f, 'medical_documents', {'pdf', 'png', 'jpg', 'jpeg'})
        if url:
            doc = PatientDocument(
                patient_id=patient.id,
                title=request.form.get('title') or f.filename or 'Document',
                document_type=request.form.get('document_type') or 'other',
                category=request.form.get('category') or 'Other',
                file_url=url, uploaded_by=current_user.id,
            )
            db.session.add(doc)
            db.session.flush()
            from app.services.timeline import record_event
            record_event(patient.id, 'DOCUMENT', f'Document uploaded: {doc.title}',
                         doc.category or doc.document_type,
                         source_type='patient_document', source_id=doc.id,
                         department='Patient Portal')
            log_activity('UPLOAD_DOCUMENT', 'patient_document', doc.id, url)
            db.session.commit()
            flash('Document uploaded successfully.', 'success')
        else:
            flash('Please choose a valid document to upload.', 'danger')
        return redirect(url_for('patient.documents'))
    files = PatientDocument.query.filter_by(patient_id=patient.id).order_by(
        PatientDocument.uploaded_at.desc()).all()
    return render_template('patient/documents.html', title='Medical Documents',
                           files=files, patient=patient)


def _document_path(doc):
    """Resolve the on-disk path for a stored document from its file_url.

    Supports both the legacy public path (``/static/uploads/...``) and the
    current private layout (a path relative to UPLOAD_FOLDER)."""
    rel = (doc.file_url or '').lstrip('/')
    if rel.startswith('static/uploads/'):
        rel = rel[len('static/uploads/'):]
        return os.path.join(current_app.static_folder, 'uploads', rel)
    base = current_app.config.get('UPLOAD_FOLDER') or 'var/uploads'
    return os.path.normpath(os.path.join(base, rel))


@patient_bp.route('/documents/<int:doc_id>/download')
@login_required
def download_document(doc_id):
    """Stream a medical document only to authorized users (owner or staff with
    documented need-to-know access). Direct static URLs are never exposed.

    ``?inline=1`` serves the file for inline viewing (image previews) instead
    of forcing a download; access control is identical.
    """
    doc = PatientDocument.query.get_or_404(doc_id)
    patient = doc.patient
    is_owner = bool(current_user.patient_profile and patient and
                     current_user.patient_profile.id == patient.id)
    from app.access import has_need_to_know
    if not (is_owner or has_need_to_know(patient)):
        abort(403)
    path = _document_path(doc)
    if not os.path.isfile(path):
        abort(404)
    log_activity('DOWNLOAD_DOCUMENT', 'patient_document', doc.id, doc.title)
    db.session.commit()
    inline = request.args.get('inline') == '1'
    return send_file(path, as_attachment=not inline,
                     download_name=os.path.basename(path))


@patient_bp.route('/messages')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def messages():
    items = Message.query.filter_by(receiver_id=current_user.id).order_by(
        Message.sent_at.desc()).limit(100).all()
    from app.models import User as UserModel
    doctors = Doctor.query.join(UserModel, Doctor.user_id == UserModel.id).order_by(UserModel.full_name).all()
    sent = Message.query.filter_by(sender_id=current_user.id).order_by(
        Message.sent_at.desc()).limit(50).all()
    return render_template('patient/messages.html', title='Messages',
                           items=items, doctors=doctors, sent=sent)


@patient_bp.route('/messages/<int:doctor_id>/compose', methods=['GET', 'POST'])
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def compose_message(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    if request.method == 'POST':
        body = (request.form.get('body') or '').strip()
        if not body:
            flash('Message body is required.', 'warning')
            return redirect(url_for('patient.compose_message', doctor_id=doctor.id))
        msg = Message(sender_id=current_user.id, receiver_id=doctor.user_id,
                      subject=(request.form.get('subject') or 'Message from patient')[:200],
                      body=body)
        db.session.add(msg)
        db.session.flush()
        from app.services.notifications import notify
        notify(doctor.user_id, f'New message: {msg.subject}',
               f'{current_user.full_name}: {body[:140]}',
               entity_type='message', entity_id=msg.id)
        db.session.commit()
        flash('Message sent.', 'success')
        return redirect(url_for('patient.messages'))
    return render_template('patient/compose_message.html', title='New Message', doctor=doctor)
