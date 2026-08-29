from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime, date
from app import db
from app.models import Appointment, Patient, Doctor, User, NursingNote
from app.routes.decorators import roles_required, log_activity

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

        appointment = Appointment(
            patient_id=request.form.get('patient_id'),
            doctor_id=request.form.get('doctor_id'),
            scheduled_at=scheduled_at,
            duration_minutes=request.form.get('duration_minutes') or 30,
            reason=request.form.get('reason'),
            priority=request.form.get('priority') or 'Normal',
            created_by=current_user.id,
        )
        db.session.add(appointment)
        log_activity('BOOK_APPOINTMENT', 'appointment', appointment.id or None,
                     f'Appointment booked by receptionist {current_user.id}')
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


@reception_bp.route('/queue')
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def queue():
    items = Appointment.query.filter(
        Appointment.status.in_(['Scheduled', 'CheckedIn'])).order_by(
        Appointment.scheduled_at).all()
    return render_template('reception/queue.html', title='Waiting Queue', items=items)


@reception_bp.route('/register')
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
def register():
    return render_template('reception/register.html', title='Patient Registration')