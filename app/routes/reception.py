from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from app import db
from app.models import Appointment, Patient, Doctor, User, NursingNote, Role
from app.routes.decorators import roles_required, log_activity
from app.utils import has_appointment_conflict, assign_mrn, utcnow
from app.services.status import assert_transition, StatusTransitionError
from app.services.timeline import record_event

reception_bp = Blueprint('reception', __name__)


@reception_bp.route('/dashboard')
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def dashboard():
    today = date.today()
    day_start = datetime.combine(today, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    todays = (Appointment.query
              .filter(Appointment.scheduled_at >= day_start,
                      Appointment.scheduled_at < day_end)
              .order_by(Appointment.scheduled_at).all())
    todays_appointments = len(todays)
    waiting_patients = sum(1 for a in todays if a.status in ('Scheduled', 'Confirmed'))
    checked_in = sum(1 for a in todays if a.status == 'CheckedIn')
    in_consultation = sum(1 for a in todays if a.status == 'InConsultation')
    completed = sum(1 for a in todays if a.status == 'Completed')
    no_shows = sum(1 for a in todays if a.status == 'NoShow')
    recent_patients = Patient.query.order_by(Patient.id.desc()).limit(8).all()
    from app.models import Admission
    active_admissions = Admission.query.filter_by(status='Admitted').count()
    return render_template('reception/dashboard.html', title='Reception Dashboard',
                           todays_appointments=todays_appointments,
                           waiting_patients=waiting_patients,
                           checked_in=checked_in, in_consultation=in_consultation,
                           completed=completed, no_shows=no_shows,
                           todays=todays, recent_patients=recent_patients,
                           active_admissions=active_admissions,
                           today=today)


@reception_bp.route('/appointments')
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def appointments():
    f_status = request.args.get('status', '').strip()
    f_date = request.args.get('date', '').strip()
    search = request.args.get('q', '').strip()
    q = Appointment.query
    if f_status:
        q = q.filter(Appointment.status == f_status)
    if f_date:
        try:
            d = datetime.strptime(f_date, '%Y-%m-%d')
            q = q.filter(Appointment.scheduled_at >= d,
                         Appointment.scheduled_at < d + timedelta(days=1))
        except ValueError:
            f_date = ''
    if search:
        q = (q.join(Patient, Appointment.patient_id == Patient.id)
              .join(User, Patient.user_id == User.id)
              .filter(db.or_(User.full_name.ilike(f'%{search}%'),
                             Patient.mrn.ilike(f'%{search}%'),
                             Patient.phone.ilike(f'%{search}%'))))
    items = q.order_by(Appointment.scheduled_at.desc()).limit(300).all()
    doctors = Doctor.query.join(User, Doctor.user_id == User.id).order_by(User.full_name).all()
    return render_template('reception/appointments.html', title='All Appointments',
                           items=items, f_status=f_status, f_date=f_date,
                           search=search, doctors=doctors)


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
        if db.session.get(Patient, patient_id) is None or db.session.get(Doctor, doctor_id) is None:
            flash('Please select a valid patient and doctor.', 'danger')
            return redirect(url_for('reception.book_appointment'))
        try:
            duration = max(5, int(request.form.get('duration_minutes') or 30))
        except (TypeError, ValueError):
            duration = 30
        visit_type = 'WalkIn' if request.form.get('visit_type') == 'WalkIn' else 'Scheduled'
        appointment = Appointment(
            patient_id=patient_id,
            doctor_id=doctor_id,
            scheduled_at=scheduled_at,
            duration_minutes=duration,
            reason=request.form.get('reason'),
            priority=request.form.get('priority') or 'Normal',
            visit_type=visit_type,
            created_by=current_user.id,
        )
        if has_appointment_conflict(doctor_id, scheduled_at, duration):
            flash('This doctor already has an appointment at that time. Please choose another slot.', 'warning')
            return redirect(url_for('reception.book_appointment'))
        db.session.add(appointment)
        db.session.flush()
        log_activity('BOOK_APPOINTMENT', 'appointment', appointment.id,
                     f'Appointment booked by receptionist {current_user.id}')
        record_event(patient_id, 'APPOINTMENT', 'Appointment booked',
                     f'With Dr. {appointment.doctor.user.full_name if appointment.doctor and appointment.doctor.user else "doctor"} on '
                     f'{scheduled_at.strftime("%d %b %Y %H:%M")}',
                     source_type='appointment', source_id=appointment.id,
                     department='Reception')
        pat = Patient.query.get(patient_id)
        if pat:
            from app.services.notifications import notify_patient
            notify_patient(pat, 'Appointment booked',
                           f'An appointment has been booked for you on '
                           f'{scheduled_at.strftime("%Y-%m-%d %H:%M")}.')
        db.session.commit()
        flash('Appointment booked successfully.', 'success')
        return redirect(url_for('reception.appointments'))

    patients = (Patient.query.join(User, Patient.user_id == User.id)
                .order_by(User.full_name).all())
    doctors = Doctor.query.join(User, Doctor.user_id == User.id).order_by(User.full_name).all()
    sel_patient = request.args.get('patient_id', type=int)
    return render_template('reception/book_appointment.html',
                           title='Book Appointment',
                           patients=patients, doctors=doctors, today=date.today(),
                           sel_patient=sel_patient)


@reception_bp.route('/appointments/<int:id>/checkin', methods=['POST'])
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def checkin(id):
    appointment = Appointment.query.get_or_404(id)
    try:
        assert_transition('appointment', appointment, 'CheckedIn')
    except StatusTransitionError:
        flash(f'Cannot check in an appointment that is {appointment.status}.', 'warning')
        return redirect(url_for('reception.appointments'))
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Arrival queue position for the day (per doctor).
    last_no = (db.session.query(db.func.max(Appointment.queue_number))
               .filter(Appointment.doctor_id == appointment.doctor_id,
                       Appointment.checked_in_at >= day_start).scalar() or 0)
    appointment.status = 'CheckedIn'
    appointment.checked_in_at = now
    appointment.queue_number = last_no + 1
    log_activity('CHECKIN_APPOINTMENT', 'appointment', appointment.id,
                 f'Patient checked in by receptionist {current_user.id} queue={appointment.queue_number}')
    if appointment.doctor and appointment.doctor.user_id:
        from app.services.notifications import notify
        notify(appointment.doctor.user_id,
               f'Patient arrived: {appointment.patient.user.full_name if appointment.patient and appointment.patient.user else "patient"}',
               f'Queue #{appointment.queue_number} · appointment #{appointment.id}',
               entity_type='appointment', entity_id=appointment.id)
    record_event(appointment.patient_id, 'VISIT', 'Patient checked in',
                 f'Appointment #{appointment.id} · waiting for '
                 + (appointment.doctor.user.full_name if appointment.doctor and appointment.doctor.user else 'the doctor'),
                 source_type='appointment', source_id=appointment.id,
                 department='Reception')
    db.session.commit()
    flash(f'Appointment #{appointment.id} checked in (queue #{appointment.queue_number}).', 'success')
    return redirect(request.referrer or url_for('reception.queue'))


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
        record_event(appointment.patient_id, 'VISIT',
                     'Visit completed',
                     f'Appointment #{appointment.id} · Dr. '
                     + (appointment.doctor.user.full_name if appointment.doctor and appointment.doctor.user else 'doctor'),
                     source_type='appointment', source_id=appointment.id,
                     department='Reception')
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


@reception_bp.route('/appointments/<int:id>/cancel', methods=['POST'])
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def cancel_appointment(id):
    appointment = Appointment.query.get_or_404(id)
    try:
        assert_transition('appointment', appointment, 'Cancelled')
    except StatusTransitionError:
        flash(f'Cannot cancel an appointment that is {appointment.status}.', 'warning')
        return redirect(url_for('reception.appointments'))
    reason = (request.form.get('reason') or 'Cancelled at reception').strip()
    appointment.status = 'Cancelled'
    log_activity('CANCEL_APPOINTMENT', 'appointment', appointment.id, reason)
    record_event(appointment.patient_id, 'APPOINTMENT', 'Appointment cancelled',
                 f'Appointment #{appointment.id} · {reason}',
                 source_type='appointment', source_id=appointment.id,
                 department='Reception')
    from app.services.notifications import notify_patient, notify
    notify_patient(appointment.patient, 'Appointment cancelled',
                   f'Your appointment on {appointment.scheduled_at.strftime("%Y-%m-%d %H:%M")} was cancelled. {reason}',
                   entity_type='appointment', entity_id=appointment.id)
    if appointment.doctor and appointment.doctor.user_id:
        notify(appointment.doctor.user_id, f'Appointment #{appointment.id} cancelled', reason,
               entity_type='appointment', entity_id=appointment.id)
    db.session.commit()
    flash('Appointment cancelled.', 'success')
    return redirect(url_for('reception.appointments'))


@reception_bp.route('/appointments/<int:id>/reschedule', methods=['POST'])
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def reschedule_appointment(id):
    appointment = Appointment.query.get_or_404(id)
    if appointment.status not in ('Scheduled', 'Confirmed', 'NoShow'):
        flash(f'Cannot reschedule an appointment that is {appointment.status}.', 'warning')
        return redirect(url_for('reception.appointments'))
    try:
        new_dt = datetime.strptime(
            f"{request.form.get('date', '')} {request.form.get('time', '')}", '%Y-%m-%d %H:%M')
    except (TypeError, ValueError):
        flash('Invalid date or time format.', 'danger')
        return redirect(url_for('reception.appointments'))
    doctor_id = request.form.get('doctor_id', type=int) or appointment.doctor_id
    if has_appointment_conflict(doctor_id, new_dt, appointment.duration_minutes or 30,
                                exclude_id=appointment.id):
        flash('That slot clashes with another appointment for the doctor.', 'warning')
        return redirect(url_for('reception.appointments'))
    old_dt = appointment.scheduled_at
    appointment.scheduled_at = new_dt
    appointment.doctor_id = doctor_id
    appointment.status = 'Scheduled'
    appointment.queue_number = None
    appointment.checked_in_at = None
    log_activity('RESCHEDULE_APPOINTMENT', 'appointment', appointment.id,
                 f'{old_dt} -> {new_dt}')
    record_event(appointment.patient_id, 'APPOINTMENT', 'Appointment rescheduled',
                 f'Moved from {old_dt.strftime("%d %b %Y %H:%M") if old_dt else "-"} '
                 f'to {new_dt.strftime("%d %b %Y %H:%M")}',
                 source_type='appointment', source_id=appointment.id,
                 department='Reception')
    from app.services.notifications import notify_patient
    notify_patient(appointment.patient, 'Appointment rescheduled',
                   f'Your appointment is now on {new_dt.strftime("%Y-%m-%d %H:%M")}.',
                   entity_type='appointment', entity_id=appointment.id)
    db.session.commit()
    flash('Appointment rescheduled.', 'success')
    return redirect(url_for('reception.appointments'))


@reception_bp.route('/queue')
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def queue():
    today = date.today()
    day_start = datetime.combine(today, datetime.min.time())
    items = Appointment.query.filter(
        Appointment.status.in_(['Scheduled', 'Confirmed', 'CheckedIn', 'InConsultation']),
        Appointment.scheduled_at >= day_start,
        Appointment.scheduled_at < day_start + timedelta(days=1)).order_by(
        Appointment.queue_number.asc().nulls_last(), Appointment.scheduled_at).all()
    return render_template('reception/queue.html', title='Waiting Queue', items=items,
                           today=today)


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

        from app.models import Department
        departments = Department.query.order_by(Department.name).all()
        dup = None
        if username:
            dup = User.query.filter(User.username == username).first()
        if dup is None and email:
            dup = User.query.filter(User.email == email).first()
        if dup is not None:
            flash('A user with that username or email already exists.', 'danger')
            return render_template('reception/register.html', title='Patient Registration',
                                   departments=departments, form=request.form)
        if not full_name or not username or not password or not email:
            flash('Full name, username, email and password are required.', 'danger')
            return render_template('reception/register.html', title='Patient Registration',
                                   departments=departments, form=request.form)
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('reception/register.html', title='Patient Registration',
                                   departments=departments, form=request.form)

        user = User(username=username, email=email, full_name=full_name,
                    user_type='patient', phone=phone or None)
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
                dob_date = datetime.strptime(dob, '%Y-%m-%d').date()
            except ValueError:
                dob_date = None
        dept_id = request.form.get('department_id', type=int)
        if dept_id and db.session.get(Department, dept_id) is None:
            dept_id = None

        new_patient = Patient(
            user_id=user.id, phone=phone or None, gender=gender,
            date_of_birth=dob_date, blood_type=blood_type,
            address=address, allergies=request.form.get('allergies', '').strip() or None,
            emergency_contact=request.form.get('emergency_contact', '').strip() or None,
            department_id=dept_id,
        )
        db.session.add(new_patient)
        db.session.flush()
        assign_mrn(new_patient)
        log_activity('REGISTER_PATIENT', 'user', user.id,
                     f'Registered by receptionist {current_user.id}')
        record_event(new_patient.id, 'VISIT', 'Patient registered',
                     f'New patient record · MRN {new_patient.mrn or "-"}',
                     source_type='patient', source_id=new_patient.id,
                     department='Reception')
        db.session.commit()
        flash(f'Patient {full_name} registered successfully (MRN {new_patient.mrn}).', 'success')
        if request.form.get('book_next'):
            return redirect(url_for('reception.book_appointment', patient_id=new_patient.id))
        return redirect(url_for('reception.dashboard'))
    from app.models import Department
    departments = Department.query.order_by(Department.name).all()
    return render_template('reception/register.html', title='Patient Registration',
                           departments=departments, form={})