from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, abort, current_app
from flask_login import login_required, current_user
from datetime import datetime
import os
from app import db
from app.models import (
    Patient, Doctor, Specialty, Appointment, MedicalRecord, Prescription,
    LabOrder, RadiologyOrder, Notification, Message, Diagnosis, VitalSign,
    PatientDocument, Bill,
)
from app.routes.decorators import roles_required, log_activity, save_upload
from app.utils import has_appointment_conflict

patient_bp = Blueprint('patient', __name__)
ALLOWED = ['Patient', 'Doctor', 'Admin', 'SuperAdmin']


def _current_patient():
    return Patient.query.filter_by(user_id=current_user.id).first()


@patient_bp.route('/dashboard')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def dashboard():
    patient = _current_patient()
    if not patient:
        flash('Please complete your patient profile first.', 'warning')
        return redirect(url_for('patient.profile')) if current_user.user_type == 'patient' \
            else redirect(url_for('main.dashboard'))
    upcoming = Appointment.query.filter_by(
        patient_id=patient.id, status='Scheduled').order_by(Appointment.scheduled_at).all()
    recent_labs = LabOrder.query.filter_by(patient_id=patient.id).order_by(
        LabOrder.order_date.desc()).limit(5).all()
    prescriptions = Prescription.query.filter_by(patient_id=patient.id).limit(5).all()
    notifications = Notification.query.filter_by(
        user_id=current_user.id).order_by(Notification.created_at.desc()).limit(5).all()
    return render_template('patient/dashboard.html', title='Patient Dashboard',
                           patient=patient, upcoming=upcoming, recent_labs=recent_labs,
                           prescriptions=prescriptions, notifications=notifications)


@patient_bp.route('/profile', methods=['GET', 'POST'])
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def profile():
    patient = _current_patient()
    if not patient:
        flash('No patient profile is associated with this account.', 'warning')
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        user = current_user
        user.phone = request.form.get('phone') or user.phone
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
    return render_template('patient/profile.html', title='My Profile', patient=patient)


@patient_bp.route('/medical-history')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def medical_history():
    patient = _current_patient()
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
    patient = _current_patient()
    items = Appointment.query.filter_by(patient_id=patient.id).order_by(
        Appointment.scheduled_at.desc()).all()
    return render_template('patient/appointments.html', title='My Appointments', items=items)


@patient_bp.route('/appointments/book', methods=['GET', 'POST'])
@login_required
@roles_required('Patient', 'Receptionist', 'Admin', 'SuperAdmin')
def book_appointment():
    if request.method == 'POST':
        doctor = Doctor.query.get(request.form.get('doctor_id'))
        if not doctor:
            flash('Please select a valid doctor.', 'danger')
            return redirect(url_for('patient.book_appointment'))
        patient = _current_patient()
        if not patient:
            patient = Patient.query.get(request.form.get('patient_id'))
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
            flash('This doctor already has an appointment at that time. Please choose another slot.', 'warning')
            return redirect(url_for('patient.book_appointment'))
        appt = Appointment(
            patient_id=patient.id,
            doctor_id=doctor.id,
            scheduled_at=scheduled_at,
            reason=request.form.get('reason'),
            priority=request.form.get('priority', 'Normal'),
        )
        db.session.add(appt)
        log_activity('BOOK_APPOINTMENT', 'appointment', None, f'doctor={doctor.id}')
        db.session.commit()
        flash('Appointment booked successfully.', 'success')
        return redirect(url_for('patient.appointments'))

    doctors = Doctor.query.all()
    return render_template('patient/book_appointment.html', title='Book Appointment', doctors=doctors)


@patient_bp.route('/prescriptions')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def prescriptions():
    patient = _current_patient()
    items = Prescription.query.filter_by(patient_id=patient.id).order_by(
        Prescription.prescribed_date.desc()).all()
    return render_template('patient/prescriptions.html', title='My Prescriptions', items=items)


@patient_bp.route('/lab-results')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def lab_results():
    patient = _current_patient()
    items = LabOrder.query.filter_by(patient_id=patient.id).order_by(
        LabOrder.order_date.desc()).all()
    return render_template('patient/lab_results.html', title='Lab Results', items=items)


@patient_bp.route('/radiology-reports')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def radiology_reports():
    patient = _current_patient()
    items = RadiologyOrder.query.filter_by(patient_id=patient.id).order_by(
        RadiologyOrder.order_date.desc()).all()
    return render_template('patient/radiology_reports.html', title='Radiology Reports', items=items)


@patient_bp.route('/bills')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def bills():
    patient = _current_patient()
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
    patient = _current_patient()
    if request.method == 'POST':
        f = request.files.get('document')
        url = save_upload(f, 'medical_documents', {'pdf', 'png', 'jpg', 'jpeg'})
        if url:
            db.session.add(PatientDocument(
                patient_id=patient.id,
                title=request.form.get('title') or f.filename or 'Document',
                document_type=request.form.get('document_type') or 'other',
                file_url=url,
            ))
            log_activity('UPLOAD_DOCUMENT', 'patient_document', patient.id, url)
            db.session.commit()
            flash('Document uploaded successfully.', 'success')
        else:
            flash('Please choose a valid document to upload.', 'danger')
        return redirect(url_for('patient.documents'))
    files = PatientDocument.query.filter_by(patient_id=patient.id).order_by(
        PatientDocument.uploaded_at.desc()).all()
    return render_template('patient/documents.html', title='Medical Documents', files=files)


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
    documented need-to-know access). Direct static URLs are never exposed."""
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
    return send_file(path, as_attachment=True,
                     download_name=os.path.basename(path))


@patient_bp.route('/messages')
@login_required
def messages():
    items = Message.query.filter_by(receiver_id=current_user.id).order_by(
        Message.sent_at.desc()).all()
    doctors = Doctor.query.all()
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
        db.session.add(Message(
            sender_id=current_user.id, receiver_id=doctor.user_id,
            subject=request.form.get('subject'), body=request.form.get('body')))
        db.session.commit()
        flash('Message sent.', 'success')
        return redirect(url_for('patient.messages'))
    return render_template('patient/compose_message.html', title='New Message', doctor=doctor)
