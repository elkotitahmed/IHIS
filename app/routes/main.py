from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models import (
    User, Patient, Doctor, LabOrder, RadiologyOrder, Appointment,
    Notification, Prescription, DentalChart, TherapySession,
)

main_bp = Blueprint('main', __name__)


# ---------------------------------------------------------------------------
# Health checks (Phase 19) — lightweight, no auth, no secrets.
# ---------------------------------------------------------------------------
@main_bp.route('/health/live')
def health_live():
    """Liveness: confirms the process is running and serving requests."""
    return jsonify({'status': 'ok'}), 200


@main_bp.route('/health/ready')
def health_ready():
    """Readiness: confirms the database is reachable."""
    try:
        db.session.execute(db.text('SELECT 1'))
        db_ok = True
    except Exception:
        db_ok = False
    status = 200 if db_ok else 503
    return jsonify({
        'status': 'ok' if db_ok else 'degraded',
        'database': 'connected' if db_ok else 'unreachable',
    }), status


@main_bp.route('/')
@main_bp.route('/home')
def home():
    return render_template('index.html', title='Home')


ROLE_HOME = {
    'SuperAdmin': 'super_admin.dashboard',
    'Admin': 'admin.dashboard',
    'Doctor': 'doctor.dashboard',
    'Patient': 'patient.dashboard',
    'Nurse': 'nursing.dashboard',
    'LabTechnician': 'lab.dashboard',
    'Radiologist': 'radiology.dashboard',
    'RadiologyTechnician': 'radiology.dashboard',
    'Pharmacist': 'pharmacy.dashboard',
    'Receptionist': 'reception.dashboard',
    'Cashier': 'billing.dashboard',
    'Dentist': 'dentistry.dashboard',
    'Physiotherapist': 'physiotherapy.dashboard',
}

# Priority order when a user carries several roles.
_ROLE_PRIORITY = ('SuperAdmin', 'Admin', 'Doctor', 'Patient', 'Nurse',
                  'LabTechnician', 'Radiologist', 'RadiologyTechnician',
                  'Pharmacist', 'Receptionist', 'Cashier', 'Dentist',
                  'Physiotherapist')

_USER_TYPE_HOME = {
    'doctor': 'doctor.dashboard', 'patient': 'patient.dashboard',
    'nurse': 'nursing.dashboard',
}


def _role_home():
    """Route authenticated users to their portal based on role.

    A SuperAdmin previewing another role lands on *that* role's workspace so
    the preview is faithful end-to-end."""
    from flask import session
    preview = session.get('preview_role')
    if preview and current_user.has_role('SuperAdmin') and preview in ROLE_HOME:
        return ROLE_HOME[preview]
    for role in _ROLE_PRIORITY:
        if current_user.has_role(role):
            return ROLE_HOME[role]
    return _USER_TYPE_HOME.get(current_user.user_type, 'main.home')


@main_bp.route('/dashboard')
@login_required
def dashboard():
    return redirect(url_for(_role_home()))


@main_bp.route('/notifications')
@login_required
def notifications():
    unchecked = request.args.get('category', '').strip()
    query = Notification.query.filter_by(user_id=current_user.id)
    if unchecked and unchecked in ('in-app', 'sms', 'email'):
        query = query.filter_by(notification_type=unchecked)
    notifs = query.order_by(Notification.created_at.desc()).limit(50).all()
    # Opening the centre reads what is shown: the bell badge reflects only what
    # the user has not seen yet.
    unread_ids = {n.id for n in notifs if not n.is_read}
    if unread_ids:
        for n in notifs:
            n.is_read = True
        db.session.commit()
    return render_template('notifications.html', title='Notifications',
                           notifications=notifs, category=unchecked, unread_ids=unread_ids)


@main_bp.route('/notifications/mark-all-read', methods=['POST'])
@login_required
def notifications_mark_all_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update(
        {'is_read': True}, synchronize_session=False)
    db.session.commit()
    flash('All notifications marked as read.', 'success')
    return redirect(url_for('main.notifications'))
