"""Unified clinical timeline service.

Every meaningful patient event across departments (registration, vitals, lab,
radiology, pharmacy, referrals, admissions, documents, tasks, follow-ups) is
recorded here so a patient's record reads as one connected story instead of a
set of per-portal silos.
"""
from flask_login import current_user

from app import db
from app.models import TimelineEvent
from app.utils import utcnow


def record_event(patient_id, event_type, title, description=None,
                 source_type=None, source_id=None, department=None,
                 occurred_at=None):
    """Persist a timeline event and return it.

    Safe to call from any route; the event does not gate access by itself —
    access is enforced by the viewing route's patient-access checks.
    """
    event = TimelineEvent(
        patient_id=patient_id, event_type=event_type, title=title,
        description=description,
        source_type=source_type, source_id=source_id, department=department,
        occurred_at=occurred_at or utcnow(),
        created_by=current_user.id if current_user.is_authenticated else None,
    )
    db.session.add(event)
    db.session.flush()
    return event


def fetch_timeline(patient_id, limit=100, types=None):
    """Recent timeline events for a patient, newest first."""
    q = TimelineEvent.query.filter_by(patient_id=patient_id)
    if types:
        q = q.filter(TimelineEvent.event_type.in_(types))
    return q.order_by(TimelineEvent.occurred_at.desc()).limit(limit).all()