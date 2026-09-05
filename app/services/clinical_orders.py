"""Single place where clinical orders are *created*.

Every entry point that writes a prescription, a lab order, an imaging order or
a referral (doctor portal, JSON API, order sets, demo scenarios) goes through
these helpers so the downstream reality is always the same:

    record  ->  safety screen  ->  work-queue task  ->  timeline event
            ->  notification to the receiving department

Nothing here commits; callers own the transaction so a notification failure
can never orphan a half-written order.
"""
from flask_login import current_user

from app import db
from app.models import (LabOrder, LabTestCatalog, ImagingType, Medication,
                        Prescription, PrescriptionItem, RadiologyOrder, Referral)
from app.services import tasks as task_svc
from app.services.notifications import notify, notify_role
from app.services.timeline import record_event
from app.utils import utcnow


def _priority_for_task(priority):
    mapping = {'Routine': 'NORMAL', 'Normal': 'NORMAL', 'Urgent': 'URGENT',
               'Stat': 'CRITICAL', 'STAT': 'CRITICAL', 'Emergency': 'CRITICAL',
               'High': 'HIGH'}
    return mapping.get(priority, str(priority or 'NORMAL').upper())


def _specialty_role(to_specialty):
    """Which role owns a referral to a given specialty."""
    text = (to_specialty or '').lower()
    if any(k in text for k in ('physio', 'rehab', 'physical therapy')):
        return 'Physiotherapist', 'Rehabilitation'
    if any(k in text for k in ('dent', 'oral', 'ortho')):
        return 'Dentist', 'Dentistry'
    return 'Doctor', 'Care Coordination'


# --------------------------------------------------------------------------
# Laboratory
# --------------------------------------------------------------------------
def create_lab_order(patient, doctor, test_id, priority='Normal',
                     specimen_type=None, notes=None, origin=None):
    """Create a lab order and route it into the laboratory queue."""
    test = db.session.get(LabTestCatalog, int(test_id))
    if test is None:
        raise ValueError('Unknown laboratory test')
    order = LabOrder(
        patient_id=patient.id,
        doctor_id=doctor.id if doctor else None,
        test_id=test.id,
        priority=priority or 'Normal',
        specimen_type=(specimen_type or 'Blood'),
        notes=notes,
    )
    db.session.add(order)
    db.session.flush()
    order.accession_number = f'LAB-{order.id:05d}'
    order.barcode = f'{order.id:08d}'
    task_svc.create_task(
        title=f'Process lab order #{order.id}: {test.test_name}',
        description=(f'{origin + ": " if origin else ""}Collect and process '
                     f'{order.specimen_type} sample; enter and verify the result.'),
        task_type='LAB', department='Laboratory', patient_id=patient.id,
        assigned_role='LabTechnician', priority=_priority_for_task(order.priority),
        related_resource_type='lab_order', related_resource_id=order.id)
    record_event(patient.id, 'LAB', f'Lab order: {test.test_name}',
                 f'{order.specimen_type} sample · priority {order.priority}'
                 + (f' · {origin}' if origin else ''),
                 source_type='lab_order', source_id=order.id,
                 department='Laboratory')
    notify_role('LabTechnician', f'New lab order #{order.id}',
                f'A new lab order ({test.test_name}) has been created for '
                f'patient {patient.mrn or "#" + str(patient.id)}.',
                entity_type='lab_order', entity_id=order.id)
    return order


# --------------------------------------------------------------------------
# Radiology
# --------------------------------------------------------------------------
def create_radiology_order(patient, doctor, imaging_type_id, priority='Normal',
                           notes=None, origin=None):
    imaging = db.session.get(ImagingType, int(imaging_type_id))
    if imaging is None:
        raise ValueError('Unknown imaging type')
    order = RadiologyOrder(
        patient_id=patient.id,
        doctor_id=doctor.id if doctor else None,
        imaging_type_id=imaging.id,
        priority=priority or 'Normal',
        notes=notes,
    )
    db.session.add(order)
    db.session.flush()
    task_svc.create_task(
        title=f'Perform study #{order.id}: {imaging.name}',
        description=(f'{origin + ": " if origin else ""}Schedule, capture, and '
                     f'prepare the study for reporting.'),
        task_type='RADIOLOGY', department='Radiology', patient_id=patient.id,
        assigned_role='RadiologyTechnician',
        priority=_priority_for_task(order.priority),
        related_resource_type='radiology_order', related_resource_id=order.id)
    record_event(patient.id, 'RADIOLOGY', f'Imaging ordered: {imaging.name}',
                 f'Priority {order.priority}' + (f' · {origin}' if origin else ''),
                 source_type='radiology_order', source_id=order.id,
                 department='Radiology')
    for role in ('RadiologyTechnician', 'Radiologist'):
        notify_role(role, f'New radiology order #{order.id}',
                    f'A new imaging order ({imaging.name}) has been created for '
                    f'patient {patient.mrn or "#" + str(patient.id)}.',
                    entity_type='radiology_order', entity_id=order.id)
    return order


# --------------------------------------------------------------------------
# Prescriptions
# --------------------------------------------------------------------------
def create_prescription(patient, doctor, items, refills=0, origin=None):
    """``items`` is a list of dicts: medication_id, dosage, frequency,
    duration, instructions, quantity. Returns the prescription or raises
    ``ValueError`` when no valid medication line is present."""
    rx = Prescription(patient_id=patient.id,
                      doctor_id=doctor.id if doctor else None,
                      refills=int(refills or 0), status='Active')
    db.session.add(rx)
    db.session.flush()
    added = 0
    for it in items or []:
        mid = it.get('medication_id')
        if not mid:
            continue
        med = db.session.get(Medication, int(mid))
        if med is None:
            continue
        try:
            qty = max(1, int(it.get('quantity') or 1))
        except (TypeError, ValueError):
            qty = 1
        db.session.add(PrescriptionItem(
            prescription_id=rx.id, medication_id=med.id,
            dosage=(it.get('dosage') or '')[:100],
            frequency=(it.get('frequency') or '')[:100],
            duration=(it.get('duration') or '')[:100],
            instructions=it.get('instructions') or '',
            quantity=qty))
        added += 1
    if added == 0:
        raise ValueError('Add at least one medication to the prescription.')
    db.session.flush()
    db.session.refresh(rx)

    # Safety screen (allergy conflicts + drug-drug interactions) -> alerts.
    from app.routes.doctor import flag_prescription_safety
    flag_prescription_safety(rx)

    task_svc.create_task(
        title=f'Dispense prescription #{rx.id}',
        description=(f'{origin + ": " if origin else ""}Review and dispense '
                     f'{added} item(s). Check interactions and stock.'),
        task_type='PHARMACY', department='Pharmacy', patient_id=patient.id,
        assigned_role='Pharmacist', priority='NORMAL',
        related_resource_type='prescription', related_resource_id=rx.id)
    record_event(patient.id, 'PRESCRIPTION',
                 f'Prescription written ({added} item(s))',
                 f'Refills: {rx.refills}' + (f' · {origin}' if origin else ''),
                 source_type='prescription', source_id=rx.id,
                 department='Doctor')
    notify_role('Pharmacist', f'New prescription #{rx.id}',
                f'A new prescription with {added} item(s) was written for '
                f'patient {patient.mrn or "#" + str(patient.id)}.',
                entity_type='prescription', entity_id=rx.id)
    return rx


# --------------------------------------------------------------------------
# Referrals
# --------------------------------------------------------------------------
def create_referral(patient, from_doctor, reason, to_specialty=None,
                    to_doctor=None, urgency='Routine', notes=None, origin=None):
    if urgency not in ('Routine', 'Urgent', 'Emergency'):
        urgency = 'Routine'
    ref = Referral(
        patient_id=patient.id,
        from_doctor_id=from_doctor.id if from_doctor else None,
        to_doctor_id=to_doctor.id if to_doctor else None,
        to_specialty=to_specialty or (to_doctor.specialty.name
                                      if to_doctor and to_doctor.specialty else None),
        reason=reason, notes=notes, status='SENT', urgency=urgency,
        created_by=current_user.id if current_user.is_authenticated else None,
    )
    db.session.add(ref)
    db.session.flush()
    target_label = (f'Dr. {to_doctor.user.full_name}' if to_doctor and to_doctor.user
                    else (ref.to_specialty or 'specialist'))
    record_event(patient.id, 'REFERRAL', f'Referral to {target_label}',
                 f'{urgency} · {reason}' + (f' · {origin}' if origin else ''),
                 source_type='referral', source_id=ref.id,
                 department='Care Coordination')
    role, department = _specialty_role(ref.to_specialty)
    priority = {'Emergency': 'CRITICAL', 'Urgent': 'HIGH'}.get(urgency, 'NORMAL')
    task_svc.create_task(
        title=f'Referral #{ref.id} — {target_label} review',
        description=f'{urgency} referral: {reason}',
        task_type='REFERRAL', department=department, patient_id=patient.id,
        assigned_to=to_doctor.user_id if to_doctor else None,
        assigned_role=role, priority=priority,
        related_resource_type='referral', related_resource_id=ref.id)
    title = f'New referral ({urgency})'
    message = (f'A {urgency.lower()} referral for '
               f'{patient.user.full_name if patient.user else "a patient"} '
               f'awaits your review.')
    if to_doctor and to_doctor.user_id:
        notify(to_doctor.user_id, title, message,
               notification_type='critical' if urgency == 'Emergency' else 'in-app',
               entity_type='referral', entity_id=ref.id)
    else:
        notify_role(role, title, message,
                    notification_type='critical' if urgency == 'Emergency' else 'in-app',
                    entity_type='referral', entity_id=ref.id)
    return ref


REFERRAL_TRANSITIONS = {
    'Pending': {'SENT', 'ACCEPTED', 'REJECTED', 'CLOSED'},
    'SENT': {'ACCEPTED', 'REJECTED', 'CLOSED'},
    'ACCEPTED': {'IN_REVIEW', 'COMPLETED', 'CLOSED'},
    'IN_REVIEW': {'COMPLETED', 'CLOSED'},
    'COMPLETED': {'CLOSED'},
    'REJECTED': {'SENT'},
    'CLOSED': set(),
}

# Legacy lower-case values written by older releases map onto the canonical
# upper-case lifecycle so the state machine keeps working on existing rows.
_LEGACY_REFERRAL = {'Accepted': 'ACCEPTED', 'Rejected': 'REJECTED',
                    'Completed': 'COMPLETED', 'Closed': 'CLOSED', 'Sent': 'SENT'}


def normalize_referral_status(value):
    if not value:
        return 'Pending'
    return _LEGACY_REFERRAL.get(value, value)


def transition_referral(ref, new_status, actor=None, response=None):
    """Apply a referral lifecycle transition with the side effects the rest of
    the hospital expects (referrer notification, task closure, timeline)."""
    new_status = normalize_referral_status(new_status)
    current = normalize_referral_status(ref.status)
    if new_status not in REFERRAL_TRANSITIONS.get(current, set()):
        raise ValueError(f'Cannot move referral from {current} to {new_status}')
    ref.status = new_status
    ref.updated_at = utcnow()
    actor = actor or (current_user if current_user.is_authenticated else None)
    if new_status in ('ACCEPTED', 'REJECTED', 'COMPLETED'):
        ref.reviewed_by = actor.id if actor else None
        ref.reviewed_at = utcnow()
    if response:
        ref.response = response
        ref.response_date = utcnow()
    record_event(ref.patient_id, 'REFERRAL',
                 f'Referral {new_status.lower().replace("_", " ")}',
                 (ref.to_specialty or 'Specialist')
                 + (f' · {response[:120]}' if response else ''),
                 source_type='referral', source_id=ref.id,
                 department='Care Coordination')
    if new_status in ('COMPLETED', 'REJECTED', 'CLOSED'):
        if new_status == 'COMPLETED':
            task_svc.complete_for_resource('referral', ref.id, 'Referral completed')
        else:
            task_svc.cancel_for_resource('referral', ref.id, f'Referral {new_status.lower()}')
    # Keep the referring doctor in the loop.
    referrer = ref.from_doctor.user_id if ref.from_doctor else None
    if referrer and (actor is None or referrer != actor.id):
        who = actor.full_name if actor else 'The receiving service'
        notify(referrer, f'Referral #{ref.id} {new_status.lower().replace("_", " ")}',
               f'{who} marked the {ref.to_specialty or "specialist"} referral as '
               f'{new_status.lower().replace("_", " ")}.'
               + (f' Response: {response[:140]}' if response else ''),
               entity_type='referral', entity_id=ref.id)
    return ref
