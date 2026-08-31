"""Shared utilities for iHIS."""
import re
from datetime import datetime, timedelta, timezone


def utcnow():
    """Return the current UTC time as a naive datetime.

    `datetime.utcnow()` is deprecated since Python 3.12. This helper keeps the
    value naive (so SQLite storage and existing naive-vs-naive comparisons keep
    working) while avoiding the deprecation warning.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Clinical records are immutable once they reach a terminal state. "Verified"
# (lab) and "Signed" (radiology) lock the record; amendments must go through an
# explicit, audited flow rather than overwriting the original.
LOCKED_STATUSES = ('Verified', 'Signed', 'Locked')


def is_clinical_locked(record):
    """Return True if a clinical record (LabResult/RadiologyReport/...) has been
    verified/signed/locked and therefore cannot be edited directly."""
    return getattr(record, 'status', None) in LOCKED_STATUSES


# ---------------------------------------------------------------------------
# Lab result abnormality evaluation
# ---------------------------------------------------------------------------
_RANGE_RE = re.compile(r'^\s*(?P<op><|<=|>|>=)\s*(?P<val>\d+(?:\.\d+)?)\s*$')
_RANGE_PAIR_RE = re.compile(
    r'^\s*(?P<lo>\d+(?:\.\d+)?)\s*[-–]\s*(?P<hi>\d+(?:\.\d+)?)\s*$')


def evaluate_lab_abnormality(result_value, normal_range):
    """Decide whether a numeric lab result is outside its reference range.

    ``normal_range`` may be a pair such as ``"70-100"`` or ``"4.0 - 5.4"``, a
    one-sided bound such as ``">10"`` / ``"<=5"``, or free text (e.g.
    ``"Negative"``). Returns a tri-state: ``True`` (abnormal), ``False``
    (within range) or ``None`` (cannot be evaluated — no numeric range or a
    non-numeric result). Callers treat ``None`` as "unknown / no reference".
    """
    if not normal_range or not result_value:
        return None
    try:
        value = float(str(result_value).strip())
    except (TypeError, ValueError):
        return None
    nr = str(normal_range).strip()
    m = _RANGE_PAIR_RE.match(nr)
    if m:
        lo, hi = float(m.group('lo')), float(m.group('hi'))
        return value < lo or value > hi
    m = _RANGE_RE.match(nr)
    if m:
        op, bound = m.group('op'), float(m.group('val'))
        return {'<': value >= bound, '<=': value > bound,
                '>': value <= bound, '>=': value < bound}[op]
    # Free-text reference with no parseable bound: unresolved.
    return None


def apply_lab_abnormality(result, order):
    """Set ``result.is_abnormal`` from the order's test reference range.

    Respects the explicit manual checkbox when available, otherwise derives the
    flag automatically. Returns the flag actually stored."""
    test = order.test if order is not None else None
    auto = evaluate_lab_abnormality(result.result_value,
                                    test.normal_range if test else None)
    if auto is True:
        result.is_abnormal = True
    elif auto is False:
        result.is_abnormal = False
    # auto is None -> leave whatever the operator chose.
    return result.is_abnormal


def has_appointment_conflict(doctor_id, scheduled_at, duration_minutes,
                             exclude_id=None):
    """Return True if the doctor already has an overlapping, non-cancelled
    appointment that would clash with the proposed booking."""
    if not doctor_id or not scheduled_at:
        return False
    start = scheduled_at
    end = start + timedelta(minutes=int(duration_minutes or 30))
    from app.models import Appointment
    q = Appointment.query.filter(
        Appointment.doctor_id == doctor_id,
        Appointment.status != 'Cancelled',
    )
    if exclude_id:
        q = q.filter(Appointment.id != exclude_id)
    for appt in q.all():
        if not appt.scheduled_at:
            continue
        appt_end = appt.scheduled_at + timedelta(minutes=int(appt.duration_minutes or 30))
        # overlap test: start < appt_end and end > appt.start
        if start < appt_end and end > appt.scheduled_at:
            return True
    return False

