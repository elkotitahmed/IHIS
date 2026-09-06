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


def _duplicate_exists(user_id, title, entity_type, entity_id):
    """An *unread* notification with the same title for the same resource and
    recipient already exists (a retried request or a repeated workflow step
    must not spam the inbox)."""
    if entity_type is None or entity_id is None:
        return False
    return db.session.query(Notification.id).filter_by(
        user_id=user_id, title=title, entity_type=entity_type,
        entity_id=entity_id, is_read=False).first() is not None


def notify(user_id, title, message, notification_type='in-app',
           entity_type=None, entity_id=None, dedupe=True):
    """Create an in-app notification for one user, optionally linked to a
    resource (``entity_type``/``entity_id``) so the UI can deep-link to it.

    When ``dedupe`` is on (default) and an identical unread notification is
    already linked to the same resource, nothing new is created and ``None``
    is returned."""
    if dedupe and _duplicate_exists(user_id, title, entity_type, entity_id):
        return None
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
        notif = notify(user.id, title, message, notification_type,
                       entity_type, entity_id)
        if notif is not None:
            created.append(notif)
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
    """Notify a Physician row's underlying user."""
    if doctor and doctor.user_id:
        return notify(doctor.user_id, title, message, notification_type,
                      entity_type, entity_id)
    return None


def notify_users(user_ids, title, message, notification_type='in-app',
                 entity_type=None, entity_id=None):
    """Notify an explicit set of users (deduplicated)."""
    created = []
    for uid in {u for u in user_ids if u}:
        notif = notify(uid, title, message, notification_type, entity_type, entity_id)
        if notif is not None:
            created.append(notif)
    return created


def care_team_user_ids(patient_id):
    """User ids of everyone on the patient's care team."""
    from app.models import CareTeam, CareTeamMember
    rows = (db.session.query(CareTeamMember.user_id)
            .join(CareTeam, CareTeamMember.team_id == CareTeam.id)
            .filter(CareTeam.patient_id == patient_id).all())
    return {uid for (uid,) in rows if uid}


def notify_ordering_clinicians(order, title, message, notification_type='in-app',
                               entity_type=None, entity_id=None,
                               fallback_role='Doctor'):
    """Result-ready / critical-result routing: the ordering doctor first, then
    the patient's care team; only when nobody is documented does the message
    fan out to the whole role so a result is never silently dropped."""
    targets = set()
    doctor = getattr(order, 'doctor', None)
    if doctor is not None and doctor.user_id:
        targets.add(doctor.user_id)
    targets |= care_team_user_ids(order.patient_id)
    if targets:
        return notify_users(targets, title, message, notification_type,
                            entity_type, entity_id)
    return notify_role(fallback_role, title, message, notification_type,
                       entity_type, entity_id)
