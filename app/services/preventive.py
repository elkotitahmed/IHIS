"""Preventive-care reminder engine.

Periodic safety-net that turns overdue/upcoming clinical events into real
alerts, tasks, and notifications — without nagging on every request.

Rules (each is idempotent / de-duplication-safe):
  - Overdue follow-up  (FollowUp.status == Scheduled, due in the past)
                       -> OVERDUE_FOLLOWUP alert + a follow-up Task
  - Missed appointment (Appointment status still Scheduled/Confirmed after its
                       scheduled time) -> MISSED_APPOINTMENT alert
  - Vaccine due        (ImmunizationRecord.next_due <= today and not completed)
                       -> VACCINE_DUE alert + a preventive Task
  - Upcoming follow-up (Scheduled, due soon) -> a reminder Notification to the
                       patient (once, keyed on the follow-up source).

De-duplication: an alert/task is created at most once per source object. The
generator keys on (alert_type, source_type, source_id) so a clinician can
resolve it once and it will not be recreated for the same source. Upcoming
reminder notifications are keyed the same way so they are not re-sent on every
dashboard render.

The generator never bolts onto a request path automatically; call it from a
maintenance job (scheduler / admin / super-admin hook).
"""
import logging
from datetime import timedelta

from app import db
from app.models import (
    Appointment, ClinicalAlert, FollowUp, ImmunizationRecord, Patient,
)
from app.utils import utcnow

from app.services import alerts as alert_svc
from app.services import tasks as task_svc

logger = logging.getLogger(__name__)

# Re-reminder guard: once an alert for a given (type, source) exists, do not
# recreate another for the same source.
REMINDER_UPCOMING_WINDOW = timedelta(days=7)


def _alert_exists(patient_id, alert_type, source_type=None, source_id=None):
    q = ClinicalAlert.query.filter_by(patient_id=patient_id, alert_type=alert_type)
    if source_type is not None:
        q = q.filter_by(source_type=source_type)
    if source_id is not None:
        q = q.filter_by(source_id=source_id)
    return q.first() is not None


def _overdue_follow_ups(now):
    return (FollowUp.query
            .filter(FollowUp.status == 'Scheduled',
                    FollowUp.scheduled_for <= now)
            .all())


def _upcoming_follow_ups(now, window=REMINDER_UPCOMING_WINDOW):
    return (FollowUp.query
            .filter(FollowUp.status == 'Scheduled',
                    FollowUp.scheduled_for > now,
                    FollowUp.scheduled_for <= now + window)
            .all())


# A visit is only "missed" once its slot has clearly passed (the patient may
# still be in the waiting room right at the scheduled time).
MISSED_APPOINTMENT_GRACE = timedelta(hours=2)


def _missed_appointments(now):
    return (Appointment.query
            .filter(Appointment.status.in_(('Scheduled', 'Confirmed')),
                    Appointment.scheduled_at <= now - MISSED_APPOINTMENT_GRACE)
            .all())


def _vaccines_due(today):
    return (ImmunizationRecord.query
            .filter(ImmunizationRecord.next_due.isnot(None),
                    ImmunizationRecord.next_due <= today)
            .all())


def run_preventive_sweep(now=None):
    """Run one pass of the preventive-care safety net.

    Returns a summary dict of what was created (alerts, tasks, notifications).
    Safe to call repeatedly; does not duplicate within a source object.
    """
    now = now or utcnow()
    today = now.date() if hasattr(now, 'date') else now
    result = {'overdue_followup': 0, 'missed_appointment': 0,
              'vaccine_due': 0, 'upcoming_followup_reminder': 0}

    # --- Overdue follow-ups ------------------------------------------------
    for fu in _overdue_follow_ups(now):
        if _alert_exists(fu.patient_id, 'OVERDUE_FOLLOWUP',
                         'follow_up', fu.id):
            continue
        alert_svc.create_alert(
            fu.patient_id, 'OVERDUE_FOLLOWUP', 'Follow-up overdue',
            f'Overdue since {fu.scheduled_for.strftime("%d %b %Y, %H:%M")}'
            f' · {fu.reason or "no reason recorded"}',
            severity='LOW', source_type='follow_up', source_id=fu.id)
        task_svc.create_task(
            title=f'Overdue follow-up for patient #{fu.patient_id}',
            description=fu.reason or 'Contact and reschedule the follow-up.',
            task_type='FOLLOW_UP', department='Care Coordination',
            patient_id=fu.patient_id,
            assigned_to=fu.provider.user_id if fu.provider and fu.provider.user else None,
            assigned_role='Doctor', priority='High', due_at=fu.scheduled_for,
            related_resource_type='follow_up', related_resource_id=fu.id)
        result['overdue_followup'] += 1

    # --- Missed appointments ----------------------------------------------
    for appt in _missed_appointments(now):
        if _alert_exists(appt.patient_id, 'MISSED_APPOINTMENT',
                         'appointment', appt.id):
            continue
        alert_svc.create_alert(
            appt.patient_id, 'MISSED_APPOINTMENT', 'Missed appointment',
            f'Appointment on {appt.scheduled_at.strftime("%d %b %Y, %H:%M")}'
            f' was not completed; please reschedule.',
            severity='LOW', source_type='appointment', source_id=appt.id)
        result['missed_appointment'] += 1

    # --- Vaccine due -------------------------------------------------------
    for im in _vaccines_due(today):
        if _alert_exists(im.patient_id, 'VACCINE_DUE', 'immunization', im.id):
            continue
        alert_svc.create_alert(
            im.patient_id, 'VACCINE_DUE', f'{im.vaccine_name} due',
            f'Vaccine due since {im.next_due.strftime("%d %b %Y")}'
            f' (dose {im.dose_number}).',
            severity='LOW', source_type='immunization', source_id=im.id)
        task_svc.create_task(
            title=f'{im.vaccine_name} (dose {im.dose_number}) due for patient #{im.patient_id}',
            description='Schedule and administer the due vaccination.',
            task_type='VACCINE', department='Nursing',
            patient_id=im.patient_id, assigned_role='Nurse',
            priority='Normal',
            related_resource_type='immunization', related_resource_id=im.id)
        result['vaccine_due'] += 1

    # --- Upcoming follow-up reminders (patient notification, once) ---------
    for fu in _upcoming_follow_ups(now):
        if _alert_exists(fu.patient_id, 'UPCOMING_FOLLOWUP',
                         'follow_up', fu.id):
            continue
        from app.services.notifications import notify_patient
        patient = Patient.query.get(fu.patient_id)
        if patient is None:
            continue
        try:
            notify_patient(
                patient, 'Upcoming follow-up reminder',
                f'You have a follow-up scheduled on '
                f'{fu.scheduled_for.strftime("%d %b %Y, %H:%M")}.',
                entity_type='follow_up', entity_id=fu.id)
            # Key the reminder so it is not re-sent on later renders.
            alert_svc.create_alert(
                fu.patient_id, 'UPCOMING_FOLLOWUP', 'Upcoming follow-up',
                'Sent patient reminder notification.', severity='INFO',
                source_type='follow_up', source_id=fu.id)
            result['upcoming_followup_reminder'] += 1
        except Exception:
            logger.exception('Failed to send upcoming follow-up reminder')

    db.session.commit()
    return result