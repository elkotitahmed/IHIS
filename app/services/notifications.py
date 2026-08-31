"""Notification engine.

Supports:
- notify a single user (linked to a resource via entity_type/entity_id)
- notify_role: fan out to every active user holding a role
- notify_patient: notify a patient's user account
- critical notifications get a distinct ``notification_type`` so the UI can
  emphasise them
"""
from app import db
from app.models import Notification, Role, User


def notify(user_id, title, message, notification_type='in-app',
           entity_type=None, entity_id=None):
    """Create an in-app notification for one user, optionally linked to a
    resource (``entity_type``/``entity_id``) so the UI can deep-link to it."""
    notif = Notification(user_id=user_id, title=title, message=message,
                         notification_type=notification_type,
                         entity_type=entity_type, entity_id=entity_id)
    db.session.add(notif)
    return notif


def notify_role(role_name, title, message, notification_type='in-app',
                entity_type=None, entity_id=None, exclude_user_id=None):
    """Notify every active user holding ``role_name``.

    Returns the list of notifications created. Used to fan work-queue events
    (e.g. 'new lab order' -> all lab staff).
    """
    created = []
    role = Role.query.filter_by(name=role_name).first()
    if not role:
        return created
    for user in role.users:
        if not user.is_active:
            continue
        if exclude_user_id is not None and user.id == exclude_user_id:
            continue
        created.append(notify(user.id, title, message, notification_type,
                              entity_type, entity_id))
    return created


def notify_patient(patient, title, message, notification_type='in-app',
                   entity_type=None, entity_id=None):
    """Create a notification addressed to a patient's linked user account."""
    if patient and patient.user_id:
        return notify(patient.user_id, title, message, notification_type,
                      entity_type, entity_id)
    return None


def notify_doctor(doctor, title, message, notification_type='in-app',
                  entity_type=None, entity_id=None):
    """Notify a Doctor row's underlying user."""
    if doctor and doctor.user_id:
        return notify(doctor.user_id, title, message, notification_type,
                      entity_type, entity_id)
    return None
