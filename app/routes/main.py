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


def _role_home():
    """Route authenticated users to their portal based on role."""
    if current_user.has_any_role('Admin', 'SuperAdmin'):
        return 'admin.dashboard'
    if current_user.has_any_role('Doctor') or current_user.user_type == 'doctor':
        return 'doctor.dashboard'
    if current_user.has_any_role('Patient') or current_user.user_type == 'patient':
        return 'patient.dashboard'
    if current_user.has_any_role('Nurse') or current_user.user_type == 'nurse':
        return 'nursing.dashboard'
    if current_user.has_any_role('LabTechnician'):
        return 'lab.dashboard'
    if current_user.has_any_role('Radiologist'):
        return 'radiology.dashboard'
    if current_user.has_any_role('Pharmacist'):
        return 'pharmacy.dashboard'
    if current_user.has_any_role('Receptionist'):
        return 'reception.dashboard'
    if current_user.has_any_role('Cashier'):
        return 'billing.dashboard'
    if current_user.has_any_role('Dentist'):
        return 'dentistry.dashboard'
    if current_user.has_any_role('Physiotherapist'):
        return 'physiotherapy.dashboard'
    return 'main.home'


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
    return render_template('notifications.html', title='Notifications',
                           notifications=notifs, category=unchecked)


@main_bp.route('/notifications/mark-all-read', methods=['POST'])
@login_required
def notifications_mark_all_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update(
        {'is_read': True}, synchronize_session=False)
    db.session.commit()
    flash('All notifications marked as read.', 'success')
    return redirect(url_for('main.notifications'))
