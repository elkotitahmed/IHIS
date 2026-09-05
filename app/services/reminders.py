"""Clinical reminder / recall engine (native reimplementation).

Harvested from the OpenEMR recall board and OpenMRS reminders patterns: rather
than asking staff to manually chase overdue items, the engine materialises due
and overdue clinical reminders from the records that already exist in the
system — scheduled follow-ups, immunization next-due dates, and (optionally)
review items surfaced by the clinical inbox.

Design rules:
- Idempotent: rerunning a scan never duplicates an already-open reminder
  (dedupe on patient + type + source).
- Non-destructive: closing a source (follow-up completed) closes its reminder.
- No weakening of existing concurrency/safety protections: the engine only
  *reads* authoritative records and writes *new* reminder rows.
"""
from app import db
from app.models import ClinicalReminder, FollowUp, ImmunizationRecord
from app.utils import utcnow


def _open_reminder_for(patient_id, reminder_type, source_type, source_id):
    return ClinicalReminder.query.filter_by(
        patient_id=patient_id,
        reminder_type=reminder_type,
        source_type=source_type,
        source_id=source_id,
        status='OPEN').first()


def _create(patient_id, reminder_type, title, message, due_date,
            priority, source_type, source_id, actor_id=None):
    reminder = ClinicalReminder(
        patient_id=patient_id, reminder_type=reminder_type,
        title=title, message=message, due_date=due_date, priority=priority,
        status='OPEN', source_type=source_type, source_id=source_id,
        created_by=actor_id,
    )
    db.session.add(reminder)
    return reminder


def scan_due_reminders(actor_id=None):
    """Materialise due/overdue clinical reminders from real records.

    Returns the number of newly-created reminders. Caller commits.
    """
    now = utcnow()
    today = now.date()
    created = 0

    # ---- FOLLOWUP: scheduled follow-ups that are due today or overdue ----
    followups = (FollowUp.query
                 .filter(FollowUp.status == 'Scheduled')
                 .all())
    for follow in followups:
        due = follow.scheduled_for.date()
        if due > today:
            continue
        if _open_reminder_for(follow.patient_id, 'FOLLOWUP', 'follow_up', follow.id):
            continue
        overdue = due < today
        _create(
            follow.patient_id, 'FOLLOWUP',
            'Follow-up due' if not overdue else 'Follow-up overdue',
            (follow.reason or 'Follow-up visit') +
            f' — scheduled {due.isoformat()}.',
            due, 'High' if overdue else 'Normal',
            'follow_up', follow.id, actor_id)
        created += 1

    # ---- IMMUNIZATION: next dose due on or before today ----
    immunizations = (ImmunizationRecord.query
                     .filter(ImmunizationRecord.next_due.isnot(None))
                     .all())
    for immun in immunizations:
        if immun.next_due > today:
            continue
        if _open_reminder_for(immun.patient_id, 'IMMUNIZATION',
                              'immunization', immun.id):
            continue
        overdue = immun.next_due < today
        _create(
            immun.patient_id, 'IMMUNIZATION',
            'Immunization due' if not overdue else 'Immunization overdue',
            f'{immun.vaccine_name} — next dose due {immun.next_due.isoformat()} '
            f'(last dose #{immun.dose_number}).',
            immun.next_due, 'High' if overdue else 'Normal',
            'immunization', immun.id, actor_id)
        created += 1

    # ---- Close reminders whose source is no longer pending.
    # Reminders created outside the engine (source_type/source_id None) are
    # not managed by the scan and must never be auto-closed. ----
    pending_followup_ids = {
        f.id for f in FollowUp.query.filter(FollowUp.status == 'Scheduled').all()}
    for rem in (ClinicalReminder.query
                .filter(ClinicalReminder.reminder_type == 'FOLLOWUP',
                        ClinicalReminder.status == 'OPEN')
                .all()):
        if rem.source_type is None or rem.source_id is None:
            continue
        if rem.source_id not in pending_followup_ids:
            rem.status = 'DONE'
            rem.completed_at = now
            rem.completed_by = actor_id

    pending_immunization_ids = {
        i.id for i in ImmunizationRecord.query.all()}
    for rem in (ClinicalReminder.query
                .filter(ClinicalReminder.reminder_type == 'IMMUNIZATION',
                        ClinicalReminder.status == 'OPEN')
                .all()):
        if rem.source_type is None or rem.source_id is None:
            continue
        if rem.source_id not in pending_immunization_ids:
            rem.status = 'DONE'
            rem.completed_at = now
            rem.completed_by = actor_id

    return created


def open_reminders_for_patients(patient_ids, limit=None):
    """OPEN reminders for the given patient scope, due-first, priority-aware."""
    query = (ClinicalReminder.query
             .filter(ClinicalReminder.patient_id.in_(patient_ids or [-1]),
                     ClinicalReminder.status == 'OPEN')
             .order_by(ClinicalReminder.due_date.asc(),
                       ClinicalReminder.priority.desc()))
    if limit:
        query = query.limit(limit)
    return query.all()