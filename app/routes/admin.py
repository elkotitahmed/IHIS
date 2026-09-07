from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func
from app import db
from app.models import (
    User, Role, Department, Doctor, Patient, Appointment,
    Specialty, LabOrder, RadiologyOrder, Bill, Payment,
)
from app.routes.decorators import roles_required, log_activity

admin_bp = Blueprint('admin', __name__)


# ─── Dashboard ──────────────────────────────────────────────────────
@admin_bp.route('/dashboard')
@login_required
@roles_required('Admin', 'SuperAdmin')
def dashboard():
    log_activity('VIEW_ADMIN_DASHBOARD', resource='admin')

    total_patients = Patient.query.count()
    total_doctors = Doctor.query.count()
    total_users = User.query.count()
    total_departments = Department.query.count()

    today = date.today()
    appointments_today = Appointment.query.filter(
        func.date(Appointment.scheduled_at) == today
    ).count()

    # Real financial figures derived from actual billing transactions, never
    # from summing doctor consultation fees.
    revenue = db.session.query(func.sum(Payment.amount)).scalar() or 0.0
    _open_bills = Bill.query.filter(Bill.status.in_(['Unpaid', 'PartiallyPaid'])).all()
    outstanding = sum(b.balance() for b in _open_bills)

    appointments_by_status = db.session.query(
        Appointment.status, func.count(Appointment.id)
    ).group_by(Appointment.status).all()

    status_labels = [s for s, _ in appointments_by_status]
    status_counts = [c for _, c in appointments_by_status]

    role_label = 'Super Admin' if current_user.has_role('SuperAdmin') else 'Admin'

    return render_template(
        'admin/dashboard.html',
        title='Admin Dashboard',
        role_label=role_label,
        total_patients=total_patients,
        total_doctors=total_doctors,
        total_users=total_users,
        total_departments=total_departments,
        appointments_today=appointments_today,
        revenue=revenue,
        outstanding=outstanding,
        status_labels=status_labels,
        status_counts=status_counts,
    )


# ─── Capacity & no-shows (rule-based operations view; not AI) ─────
@admin_bp.route('/capacity')
@login_required
@roles_required('Admin', 'SuperAdmin')
def capacity():
    """Per-physician booked load for the next 7 days, no-show rate over the
    last 30 days and a deterministic recommendation for the operations team."""
    from datetime import datetime, timedelta
    from app.models import Appointment
    now = datetime.utcnow()
    week_end = now + timedelta(days=7)
    month_ago = now - timedelta(days=30)
    # Sun–Thu working days inside the next 7 days × 8 h
    working_days = sum(1 for i in range(7) if (now + timedelta(days=i)).weekday() not in (4, 5))
    capacity_minutes = max(working_days, 1) * 8 * 60
    rows, totals = [], {'booked': 0, 'minutes': 0, 'no_shows': 0, 'past': 0, 'over': 0}
    for doc in Doctor.query.order_by(Doctor.user_id).all():
        upcoming = Appointment.query.filter(
            Appointment.doctor_id == doc.id, Appointment.scheduled_at >= now,
            Appointment.scheduled_at < week_end,
            Appointment.status.notin_(['Cancelled', 'NoShow'])).all()
        booked = len(upcoming)
        minutes = sum((a.duration_minutes or 30) for a in upcoming)
        past = Appointment.query.filter(
            Appointment.doctor_id == doc.id, Appointment.scheduled_at >= month_ago,
            Appointment.scheduled_at < now,
            Appointment.status.in_(['Completed', 'NoShow', 'Cancelled', 'InConsultation'])).all()
        no_shows = sum(1 for a in past if a.status == 'NoShow')
        no_show_rate = round(100 * no_shows / len(past)) if past else 0
        utilisation = round(100 * minutes / capacity_minutes)
        if utilisation >= 85:
            rec = ('Near capacity: add a session or redistribute follow-ups.',
                   'قرب السعة القصوى: أضف جلسة أو وزّع المتابعات.')
            totals['over'] += 1
        elif no_show_rate >= 20:
            rec = ('High no-show rate: enable reminders and confirmation calls.',
                   'معدل غياب مرتفع: فعّل التذكيرات ومكالمات التأكيد.')
        elif utilisation <= 30 and booked:
            rec = ('Under-used: consider merging clinics or opening walk-in slots.',
                   'استغلال منخفض: فكّر في دمج العيادات أو فتح مواعيد بدون حجز.')
        elif not booked:
            rec = ('No bookings in the next 7 days.', 'لا حجوزات في الأيام السبعة القادمة.')
        else:
            rec = ('Balanced.', 'متوازن.')
        rows.append({'doctor': doc, 'booked': booked, 'minutes': minutes, 'utilisation': utilisation,
                     'no_shows': no_shows, 'past': len(past), 'no_show_rate': no_show_rate,
                     'recommendation': rec[0], 'recommendation_ar': rec[1]})
        totals['booked'] += booked; totals['minutes'] += minutes
        totals['no_shows'] += no_shows; totals['past'] += len(past)
    totals['no_show_rate'] = round(100 * totals['no_shows'] / totals['past']) if totals['past'] else 0
    rows.sort(key=lambda r: -r['utilisation'])
    # Predicted no-shows for the next 7 days (own-data logistic model; honest when data is thin)
    from app.services import noshow
    upcoming = Appointment.query.filter(
        Appointment.scheduled_at >= now, Appointment.scheduled_at < week_end,
        Appointment.status.notin_(['Cancelled', 'NoShow', 'Completed'])).order_by(Appointment.scheduled_at).all()
    risk = noshow.predict(upcoming)
    at_risk = sorted([a for a in upcoming if risk.get(a.id, 0) >= 0.5], key=lambda a: -risk[a.id])[:20]
    return render_template('admin/capacity.html', title='Capacity & no-shows', rows=rows, totals=totals,
                           noshow=noshow.status(), risk=risk, at_risk=at_risk)


@admin_bp.route('/ai/appointment-optimization')
@login_required
def appointment_optimization():
    """Retired: the one-threshold 'AI optimisation' page. Redirects to the capacity view."""
    return redirect(url_for('admin.capacity'))


# ─── Code staff list remains below ──────────────────────────────────
@admin_bp.route('/staff')
@login_required
@roles_required('Admin', 'SuperAdmin')
def staff():
    log_activity('VIEW_STAFF_LIST', resource='admin')
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/staff.html', title='Staff Management', users=users)


# ─── Manage Single Staff Member ─────────────────────────────────────
@admin_bp.route('/staff/<int:user_id>/manage')
@login_required
@roles_required('Admin', 'SuperAdmin')
def manage_staff(user_id):
    log_activity('VIEW_STAFF_DETAILS', resource='user', resource_id=user_id)
    user = User.query.get_or_404(user_id)
    all_roles = Role.query.order_by(Role.name).all()
    return render_template(
        'admin/manage_staff.html',
        title=f'Manage Staff - {user.full_name}',
        user=user,
        all_roles=all_roles,
    )


@admin_bp.route('/staff/<int:user_id>/roles', methods=['POST'])
@login_required
@roles_required('Admin', 'SuperAdmin')
def update_roles(user_id):
    user = User.query.get_or_404(user_id)
    role_id = request.form.get('role_id', type=int)
    action = request.form.get('action', 'add')

    if role_id:
        role = Role.query.get(role_id)
        if role:
            # Privilege-escalation guard: only a SuperAdmin may grant or
            # revoke the SuperAdmin role (prevents an Admin escalating
            # themselves or others to full system control).
            if role.name == 'SuperAdmin' and not current_user.has_role('SuperAdmin'):
                flash('Only a SuperAdmin can assign or remove the SuperAdmin role.', 'danger')
                return redirect(url_for('admin.manage_staff', user_id=user.id))
            # A regular Admin cannot elevate a user to Administrator level either.
            if (role.name == 'Admin'
                    and not current_user.has_role('SuperAdmin')
                    and current_user.id == user.id):
                flash('You cannot assign the Admin role to yourself.', 'danger')
                return redirect(url_for('admin.manage_staff', user_id=user.id))

            if action == 'add' and role not in user.roles:
                user.roles.append(role)
                log_activity('ADD_ROLE_TO_USER', resource='user', resource_id=user.id,
                             details=f'{role.name}')
                flash(f'Role "{role.name}" added to {user.full_name}.', 'success')
            elif action == 'remove' and role in user.roles:
                user.roles.remove(role)
                log_activity('REMOVE_ROLE_FROM_USER', resource='user', resource_id=user.id,
                             details=f'{role.name}')
                flash(f'Role "{role.name}" removed from {user.full_name}.', 'warning')
            db.session.commit()
        else:
            flash('Role not found.', 'danger')

    return redirect(url_for('admin.manage_staff', user_id=user.id))


# ─── Departments ────────────────────────────────────────────────────
@admin_bp.route('/departments', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'SuperAdmin')
def departments():
    doctors = Doctor.query.join(User).order_by(User.full_name).all()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        head_doctor_id = request.form.get('head_doctor_id', type=int)

        if not name:
            flash('Department name is required.', 'danger')
        elif Department.query.filter_by(name=name).first():
            flash('A department with that name already exists.', 'warning')
        else:
            dept = Department(name=name, description=description)
            if head_doctor_id:
                dept.head_doctor_id = head_doctor_id
            db.session.add(dept)
            db.session.commit()
            log_activity('CREATE_DEPARTMENT', resource='department', resource_id=dept.id,
                         details=name)
            db.session.commit()
            flash(f'Department "{name}" created successfully.', 'success')
            return redirect(url_for('admin.departments'))
    else:
        log_activity('VIEW_DEPARTMENTS', resource='admin')

    departments_list = Department.query.order_by(Department.name).all()
    doctor_map = {doc.id: doc for doc in doctors}
    return render_template(
        'admin/departments.html',
        title='Departments',
        departments=departments_list,
        doctors=doctors,
        doctor_map=doctor_map,
    )


# ─── Retired: 12-keyword "coding assistant". The EMR diagnosis field has the
#     real ICD-10 lookup; Gemini coding lives at /ai/medical-coding/<patient>.
@admin_bp.route('/ai/coding-assistant', methods=['GET', 'POST'])
@login_required
def coding_assistant():
    return redirect(url_for('ai.ai_hub') + '#hub-documentation')


# ─── Doctors ────────────────────────────────────────────────────────
@admin_bp.route('/doctors')
@login_required
@roles_required('Admin', 'SuperAdmin')
def doctors():
    log_activity('VIEW_DOCTORS', resource='admin')
    doctors_list = (
        Doctor.query
        .join(User, Doctor.user_id == User.id)
        .outerjoin(Specialty, Doctor.specialty_id == Specialty.id)
        .order_by(User.full_name)
        .all()
    )
    return render_template(
        'admin/doctors.html',
        title='Doctors',
        doctors=doctors_list,
    )


# ─── Statistics ─────────────────────────────────────────────────────
@admin_bp.route('/statistics')
@login_required
@roles_required('Admin', 'SuperAdmin')
def statistics():
    """Retired duplicate of /reports/statistics."""
    return redirect(url_for('reports.statistics'))


