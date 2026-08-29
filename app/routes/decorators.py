"""Security helpers: RBAC decorators, audit logging, file upload validation."""
from functools import wraps
from flask import abort, request, redirect, url_for, flash
from flask_login import current_user
from app import db
from app.models import AuditLog, Permission
from werkzeug.utils import secure_filename
import os


def roles_required(*roles):
    """Require the current user to have any one of the given roles."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login', next=request.path))
            if not current_user.has_any_role(*roles):
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def roles_any(*roles):
    return roles_required(*roles)


def permissions_required(*permission_names):
    """Require the current user (via any role) to hold every named permission."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login', next=request.path))
            perms = set()
            for role in current_user.roles:
                for perm in role.permissions:
                    perms.add(perm.name)
            if not all(p in perms for p in permission_names):
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def log_activity(action, resource=None, resource_id=None, details=None):
    """Write an audit log entry for the current user."""
    entry = AuditLog(
        user_id=current_user.get_id() if current_user.is_authenticated else None,
        action=action,
        resource=resource,
        resource_id=resource_id,
        details=details,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string[:250] if request.user_agent else None,
    )
    db.session.add(entry)


def allowed_file(filename, allowed_extensions):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in allowed_extensions


def save_upload(file_storage, subfolder, allowed_extensions):
    """Securely save an uploaded file, returning its URL path or None."""
    if file_storage is None or file_storage.filename == '':
        return None
    if not allowed_file(file_storage.filename, allowed_extensions):
        abort(400, description='File type not allowed')

    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'uploads')
    dest_dir = os.path.join(base, subfolder)
    os.makedirs(dest_dir, exist_ok=True)
    filename = secure_filename(file_storage.filename)
    path = os.path.join(dest_dir, filename)
    file_storage.save(path)
    return f'/static/uploads/{subfolder}/{filename}'
