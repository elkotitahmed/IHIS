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


# ─── Staff List ─────────────────────────────────────────────────────
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
    log_activity('VIEW_STATISTICS', resource='admin')

    total_patients = Patient.query.count()
    total_doctors = Doctor.query.count()
    total_appointments = Appointment.query.count()
    total_lab_orders = LabOrder.query.count()
    total_radiology_orders = RadiologyOrder.query.count()
    total_users = User.query.count()

    appointments_by_status = db.session.query(
        Appointment.status, func.count(Appointment.id)
    ).group_by(Appointment.status).all()

    lab_by_status = db.session.query(
        LabOrder.status, func.count(LabOrder.id)
    ).group_by(LabOrder.status).all()

    radiology_by_status = db.session.query(
        RadiologyOrder.status, func.count(RadiologyOrder.id)
    ).group_by(RadiologyOrder.status).all()

    return render_template(
        'admin/statistics.html',
        title='Statistics',
        total_patients=total_patients,
        total_doctors=total_doctors,
        total_appointments=total_appointments,
        total_lab_orders=total_lab_orders,
        total_radiology_orders=total_radiology_orders,
        total_users=total_users,
        appointments_by_status=appointments_by_status,
        lab_by_status=lab_by_status,
        radiology_by_status=radiology_by_status,
    )
