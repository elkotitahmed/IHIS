"""Super Admin portal - full system control."""
import os
import shutil

from flask import Blueprint, request, redirect, url_for, flash, render_template, abort, current_app
from flask_login import login_required, current_user

from app import db
from app.models import (
    User, Role, Permission, AuditLog, SystemSetting, Patient, Doctor,
)
from app.routes.decorators import roles_required, permissions_required, log_activity
from app.permissions import ADMIN_MANAGE_PERMISSIONS, ADMIN_MANAGE_ROLES, ADMIN_MANAGE_USERS
from app.utils import utcnow

super_admin_bp = Blueprint('super_admin', __name__)

DASHBOARD_ACTIONS = (
    "LOGIN_FAILED",
    "LOGIN_FAILURE",
    "AUTH_FAILED",
    "PASSWORD_RESET_FAILED",
)


@super_admin_bp.route('/dashboard')
@login_required
@roles_required('SuperAdmin')
def dashboard():
    active_users = User.query.filter_by(is_active=True).count()
    total_users = User.query.count()
    total_patients = Patient.query.count()
    total_doctors = Doctor.query.count()
    total_roles = Role.query.count()
    total_permissions = Permission.query.count()
    total_audit_logs = AuditLog.query.count()
    total_settings = SystemSetting.query.count()

    security_alerts = AuditLog.query.filter(
        AuditLog.action.in_(DASHBOARD_ACTIONS)
    ).count()

    return render_template(
        'super_admin/dashboard.html',
        title='Super Admin Dashboard',
        active_users=active_users,
        total_users=total_users,
        total_patients=total_patients,
        total_doctors=total_doctors,
        total_roles=total_roles,
        total_permissions=total_permissions,
        total_audit_logs=total_audit_logs,
        total_settings=total_settings,
        security_alerts=security_alerts,
        system_health='Operational',
        error_log_count=0,
    )


@super_admin_bp.route('/users')
@login_required
@roles_required('SuperAdmin')
def users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('super_admin/users.html', title='Manage Users', users=users)


@super_admin_bp.route('/users/<int:user_id>/toggle', methods=['POST'])
@login_required
@roles_required('SuperAdmin')
def toggle_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404, description='User not found')
    if user.id == current_user.id:
        flash('You cannot deactivate your own account.', 'danger')
        return redirect(url_for('super_admin.users'))

    user.is_active = not user.is_active
    db.session.commit()
    status = 'activated' if user.is_active else 'deactivated'
    log_activity('TOGGLE_USER', 'User', user.id, f'{user.username}: {status}')
    db.session.commit()
    flash(f'User "{user.username}" has been {status}.', 'success')
    return redirect(url_for('super_admin.users'))


@super_admin_bp.route('/users/<int:user_id>/roles', methods=['GET', 'POST'])
@login_required
@roles_required('SuperAdmin')
def user_roles(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404, description='User not found')

    if request.method == 'POST':
        role_name = request.form.get('role_name', '').strip()
        if role_name:
            role = Role.query.filter_by(name=role_name).first()
            if role is None:
                role = Role(name=role_name, description=f'Auto-created role {role_name}')
                db.session.add(role)
                db.session.flush()
            if role not in user.roles:
                user.roles.append(role)
                db.session.commit()
                log_activity('ADD_ROLE_TO_USER', 'UserRole', user.id,
                             f'{user.username} <- {role.name}')
                db.session.commit()
                flash(f'Role "{role.name}" added to "{user.username}".', 'success')
            else:
                flash(f'User already has the role "{role.name}".', 'warning')
        else:
            flash('Role name cannot be empty.', 'danger')
        return redirect(url_for('super_admin.user_roles', user_id=user.id))

    all_roles = Role.query.order_by(Role.name).all()
    return render_template(
        'super_admin/user_roles.html',
        title='User Roles',
        user=user,
        all_roles=all_roles,
    )


@super_admin_bp.route('/roles', methods=['GET', 'POST'])
@login_required
@roles_required('SuperAdmin')
def roles():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        if not name:
            flash('Role name is required.', 'danger')
        else:
            role = Role.query.filter_by(name=name).first()
            if role:
                flash(f'Role "{name}" already exists.', 'warning')
            else:
                role = Role(name=name, description=description)
                db.session.add(role)
                db.session.commit()
                log_activity('CREATE_ROLE', 'Role', role.id, f'Role: {name}')
                db.session.commit()
                flash(f'Role "{name}" created successfully.', 'success')
        return redirect(url_for('super_admin.roles'))

    roles = Role.query.order_by(Role.name).all()
    return render_template('super_admin/roles.html', title='Manage Roles', roles=roles)


@super_admin_bp.route('/permissions', methods=['GET', 'POST'])
@login_required
@roles_required('SuperAdmin')
@permissions_required(ADMIN_MANAGE_PERMISSIONS)
def permissions():
    if request.method == 'POST':
        role_id = request.form.get('role_id', type=int)
        permission_name = request.form.get('permission_name', '').strip()
        resource = request.form.get('resource', '').strip()
        action = request.form.get('action', '').strip()

        role = db.session.get(Role, role_id) if role_id else None
        if role is None:
            flash('Please select a valid role.', 'danger')
        elif not permission_name:
            flash('Permission name is required.', 'danger')
        else:
            permission = Permission.query.filter_by(name=permission_name).first()
            if permission is None:
                permission = Permission(
                    name=permission_name,
                    resource=resource or None,
                    action=action or None,
                )
                db.session.add(permission)
                db.session.flush()
            if permission not in role.permissions:
                role.permissions.append(permission)
                db.session.commit()
                log_activity('ADD_PERMISSION_TO_ROLE', 'RolePermission', role.id,
                             f'{role.name} <- {permission.name}')
                db.session.commit()
                flash(f'Permission "{permission.name}" added to role "{role.name}".', 'success')
            else:
                flash('This permission is already assigned to the role.', 'warning')
        return redirect(url_for('super_admin.permissions'))

    roles = Role.query.order_by(Role.name).all()
    permissions = Permission.query.order_by(Permission.name).all()
    return render_template(
        'super_admin/permissions.html',
        title='Manage Permissions',
        roles=roles,
        permissions=permissions,
    )


@super_admin_bp.route('/audit-logs')
@login_required
@roles_required('SuperAdmin')
def audit_logs():
    logs = db.session.query(AuditLog).outerjoin(User, AuditLog.user_id == User.id).order_by(
        AuditLog.created_at.desc()).all()
    return render_template('super_admin/audit_logs.html', title='Audit Logs', logs=logs)


@super_admin_bp.route('/settings')
@login_required
@roles_required('SuperAdmin')
def settings():
    settings = SystemSetting.query.order_by(SystemSetting.category, SystemSetting.key).all()
    return render_template('super_admin/settings.html', title='System Settings', settings=settings)


@super_admin_bp.route('/backup', methods=['GET', 'POST'])
@login_required
@roles_required('SuperAdmin')
def backup():
    if request.method == 'POST':
        db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
        db_path = db_uri.replace('sqlite:///', '', 1)
        db_path = os.path.normpath(db_path)

        backups_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'backups'
        )
        os.makedirs(backups_dir, exist_ok=True)

        timestamp = utcnow().strftime('%Y%m%d_%H%M%S')
        base, ext = os.path.splitext(os.path.basename(db_path))
        dest_path = os.path.join(backups_dir, f'{base}_{timestamp}{ext}')

        shutil.copy2(db_path, dest_path)
        log_activity('BACKUP_DATABASE', 'Database', None, f'Backup -> {dest_path}')
        db.session.commit()
        flash(f'Database backup created successfully at {os.path.basename(dest_path)}.', 'success')
        return redirect(url_for('super_admin.backup'))

    db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    db_path = db_uri.replace('sqlite:///', '', 1)
    db_name = os.path.basename(db_path)
    backup_folder = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'backups'
    )
    return render_template(
        'super_admin/backup.html',
        title='Database Backup',
        db_name=db_name,
        backup_folder=backup_folder,
    )
