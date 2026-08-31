from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime, date
from app import db
from app.models import Appointment, Patient, Doctor, User, NursingNote, Role
from app.routes.decorators import roles_required, log_activity
from app.utils import has_appointment_conflict

reception_bp = Blueprint('reception', __name__)


@reception_bp.route('/dashboard')
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def dashboard():
    today = date.today()
    todays_appointments = Appointment.query.filter(
        db.func.date(Appointment.scheduled_at) == today).count()
    waiting_patients = Appointment.query.filter_by(status='Scheduled').count()
    checked_in = Appointment.query.filter_by(status='CheckedIn').count()
    return render_template('reception/dashboard.html', title='Reception Dashboard',
                           todays_appointments=todays_appointments,
                           waiting_patients=waiting_patients,
                           checked_in=checked_in,
                           today=today)


@reception_bp.route('/appointments')
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def appointments():
    items = Appointment.query.order_by(Appointment.scheduled_at).all()
    return render_template('reception/appointments.html', title='All Appointments',
                           items=items)


@reception_bp.route('/appointments/book', methods=['GET', 'POST'])
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def book_appointment():
    if request.method == 'POST':
        scheduled_at = None
        try:
            scheduled_at = datetime.strptime(
                f"{request.form.get('date', '')} {request.form.get('time', '')}",
                '%Y-%m-%d %H:%M')
        except (TypeError, ValueError):
            flash('Invalid date or time format.', 'danger')
            return redirect(url_for('reception.book_appointment'))

        try:
            patient_id = int(request.form.get('patient_id'))
            doctor_id = int(request.form.get('doctor_id'))
        except (TypeError, ValueError):
            flash('Please select a valid patient and doctor.', 'danger')
            return redirect(url_for('reception.book_appointment'))
        appointment = Appointment(
            patient_id=patient_id,
            doctor_id=doctor_id,
            scheduled_at=scheduled_at,
            duration_minutes=request.form.get('duration_minutes') or 30,
            reason=request.form.get('reason'),
            priority=request.form.get('priority') or 'Normal',
            created_by=current_user.id,
        )
        duration = int(request.form.get('duration_minutes') or 30)
        if has_appointment_conflict(doctor_id, scheduled_at, duration):
            flash('This doctor already has an appointment at that time. Please choose another slot.', 'warning')
            return redirect(url_for('reception.book_appointment'))
        db.session.add(appointment)
        db.session.flush()
        log_activity('BOOK_APPOINTMENT', 'appointment', appointment.id,
                     f'Appointment booked by receptionist {current_user.id}')
        pat = Patient.query.get(patient_id)
        if pat:
            from app.services.notifications import notify_patient
            notify_patient(pat, 'Appointment booked',
                           f'An appointment has been booked for you on '
                           f'{scheduled_at.strftime("%Y-%m-%d %H:%M")}.')
        db.session.commit()
        flash('Appointment booked successfully.', 'success')
        return redirect(url_for('reception.appointments'))

    patients = Patient.query.all()
    doctors = Doctor.query.all()
    return render_template('reception/book_appointment.html',
                           title='Book Appointment',
                           patients=patients, doctors=doctors, today=date.today())


@reception_bp.route('/appointments/<int:id>/checkin', methods=['POST'])
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def checkin(id):
    appointment = Appointment.query.get_or_404(id)
    appointment.status = 'CheckedIn'
    log_activity('CHECKIN_APPOINTMENT', 'appointment', appointment.id,
                 f'Patient checked in by receptionist {current_user.id}')
    db.session.commit()
    flash(f'Appointment #{appointment.id} checked in.', 'success')
    return redirect(url_for('reception.appointments'))


@reception_bp.route('/appointments/<int:id>/status', methods=['POST'])
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def set_status(id):
    """Mark a patient as NoShow for Scheduled/Confirmed, or Complete a
    CheckedIn visit (auto-generating the consultation bill)."""
    appointment = Appointment.query.get_or_404(id)
    mode = request.form.get('mode', 'NoShow')
    if mode == 'Complete' and appointment.status == 'CheckedIn':
        appointment.status = 'Completed'
        from app.services.billing import ensure_bill_for_consultation
        bill = ensure_bill_for_consultation(appointment.id, appointment.patient_id,
                                            appointment.doctor_id)
        if bill:
            log_activity('AUTO_BILL_CONSULTATION', 'bill', bill.id,
                         f'appointment={appointment.id}')
        log_activity('COMPLETE_APPOINTMENT', 'appointment', appointment.id)
        db.session.commit()
        flash('Visit completed; consultation bill generated.', 'success')
    else:
        if appointment.status in ('Scheduled', 'Confirmed'):
            appointment.status = 'NoShow'
            log_activity('NO_SHOW_APPOINTMENT', 'appointment', appointment.id)
            db.session.commit()
            flash('Appointment marked as no-show.', 'success')
        else:
            flash('Cannot mark this appointment as no-show.', 'warning')
    return redirect(url_for('reception.appointments'))


@reception_bp.route('/queue')
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def queue():
    items = Appointment.query.filter(
        Appointment.status.in_(['Scheduled', 'CheckedIn'])).order_by(
        Appointment.scheduled_at).all()
    return render_template('reception/queue.html', title='Waiting Queue', items=items)


@reception_bp.route('/register', methods=['GET', 'POST'])
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def register():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        phone = request.form.get('phone', '').strip()
        gender = request.form.get('gender', '').strip() or None
        dob = request.form.get('date_of_birth', '').strip()
        blood_type = request.form.get('blood_type', '').strip() or None
        address = request.form.get('address', '').strip() or None

        if not full_name or not username or not password:
            flash('Full name, username and password are required.', 'danger')
            return render_template('reception/register.html', title='Patient Registration')

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash('A user with that username or email already exists.', 'danger')
            return render_template('reception/register.html', title='Patient Registration')

        user = User(username=username, email=email or None, full_name=full_name,
                    user_type='patient')
        user.set_password(password)
        role = Role.query.filter_by(name='Patient').first()
        if not role:
            role = Role(name='Patient', description='Role for Patient')
            db.session.add(role)
        if role not in user.roles:
            user.roles.append(role)
        db.session.add(user)
        db.session.flush()

        dob_date = None
        if dob:
            try:
                dob_date = datetime.strptime(dob, '%Y-%m-%d')
            except ValueError:
                dob_date = None

        db.session.add(Patient(
            user_id=user.id, phone=phone or None, gender=gender,
            date_of_birth=dob_date, blood_type=blood_type,
            address=address, allergies=None,
        ))
        log_activity('REGISTER_PATIENT', 'user', user.id,
                     f'Registered by receptionist {current_user.id}')
        db.session.commit()
        flash(f'Patient {full_name} registered successfully.', 'success')
        return redirect(url_for('reception.dashboard'))
    return render_template('reception/register.html', title='Patient Registration')