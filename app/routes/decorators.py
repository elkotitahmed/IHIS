"""Security helpers: RBAC decorators, audit logging, file upload validation."""
from functools import wraps
import os
import json
import uuid

from flask import abort, request, redirect, url_for, flash, current_app
from flask_login import current_user
from app import db
from app.models import AuditLog, Permission


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


def log_change(action, resource, resource_id, old_value=None, new_value=None,
               reason=None, details=None):
    """Write an audit entry that captures the previous and new state of a
    clinical record (used for locked/verified records that are amended)."""
    def _s(v):
        if v is None:
            return None
        if isinstance(v, (dict, list)):
            return json.dumps(v, default=str, ensure_ascii=False)
        return str(v)
    entry = AuditLog(
        user_id=current_user.get_id() if current_user.is_authenticated else None,
        action=action,
        resource=resource,
        resource_id=resource_id,
        details=details,
        old_value=_s(old_value),
        new_value=_s(new_value),
        reason=reason,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string[:250] if request.user_agent else None,
    )
    db.session.add(entry)


def allowed_file(filename, allowed_extensions):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in allowed_extensions


# Leading magic bytes used to validate the real content type of uploaded files.
# The first short block a file starts with must contain one of these signatures.
_MAGIC_SIGNATURES = {
    'pdf': (b'%PDF',),
    'png': (b'\x89PNG\r\n\x1a\n',),
    'jpg': (b'\xff\xd8\xff',),
    'jpeg': (b'\xff\xd8\xff',),
    'dcm': (b'DICM',),
}


def _content_matches(filename, stream):
    ext = filename.rsplit('.', 1)[1].lower()
    signatures = _MAGIC_SIGNATURES.get(ext)
    if not signatures:
        return True  # no strict magic check for extension-only types
    header = stream.read(512)
    stream.seek(0)
    if ext == 'dcm':
        # DICOM: "DICM" either at the start or after the 128-byte preamble.
        return header.startswith(b'DICM') or (len(header) >= 132 and header[128:132] == b'DICM')
    return header.startswith(signatures[0])


def save_upload(file_storage, subfolder, allowed_extensions):
    """Securely save an uploaded file into the private UPLOAD_FOLDER.

    - UUID-based filename (original extension preserved): no predictable
      enumeration, no accidental overwrite of an existing file.
    - Magic-byte validation in addition to the extension allowlist, so a
      spoofed extension cannot be used to store arbitrary content.

    Return value: a path relative to UPLOAD_FOLDER (e.g. ``subfolder/<uuid>.pdf``)
    that download routes resolve server-side with access control. The file is
    never placed under ``static/``, so it is not publicly served.
    """
    if file_storage is None or file_storage.filename == '':
        return None
    if not allowed_file(file_storage.filename, allowed_extensions):
        abort(400, description='File type not allowed')
    if not _content_matches(file_storage.filename, file_storage.stream):
        abort(400, description='File content does not match its type')

    ext = file_storage.filename.rsplit('.', 1)[1].lower()
    base = current_app.config.get('UPLOAD_FOLDER') or 'var/uploads'
    dest_dir = os.path.join(base, subfolder)
    os.makedirs(dest_dir, exist_ok=True)
    filename = f'{uuid.uuid4().hex}.{ext}'
    path = os.path.join(dest_dir, filename)
    file_storage.save(path)
    return f'{subfolder}/{filename}'
