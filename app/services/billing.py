"""Billing hooks used by clinical routes.

When a clinical record reaches a terminal, billable state (a verified lab
result, a signed radiology report, a dispensed prescription), the revenue is
pushed into the billing subsystem automatically so the front desk only has to
accept payment rather than re-key charges.
"""
from app import db
from app.models import (
    Bill, BillItem, LabOrder, RadiologyOrder, Prescription,
    PharmacyInventory, Doctor,
)


def ensure_bill_for_consultation(appointment_id, patient_id, doctor_id=None, amount=None):
    """Generate a consultation bill for a completed visit.

    Priced from the consulting doctor's consultation_fee (falling back to the
    'General/Consultation' service catalog entry). One bill per appointment.
    """
    if amount is None:
        fee = 0.0
        if doctor_id:
            doc = db.session.get(Doctor, doctor_id)
            fee = doc.consultation_fee if doc else fee
        if fee <= 0:
            from app.models import ServiceCatalog
            svc = ServiceCatalog.query.filter_by(category='Consultation', is_active=True).first()
            fee = svc.price if svc else 0.0
        amount = fee
    if amount <= 0:
        return None
    label = f'Consultation — {doctor_name(doctor_id)}' if doctor_id else 'General Consultation'
    return _ensure_bill(patient_id, 'Consultation', appointment_id, label, 1, amount)


def doctor_name(doctor_id):
    try:
        from app.models import Doctor
        doc = db.session.get(Doctor, doctor_id)
        return f'Dr. {doc.user.full_name}' if doc and doc.user else 'Doctor'
    except Exception:
        return 'Doctor'


def ensure_bill_for_lab(order_id):
    order = db.session.get(LabOrder, order_id)
    if not order or not order.test:
        return None
    return _ensure_bill(order.patient_id, 'Lab', order.id,
                        f'Laboratory — {order.test.test_name}',
                        1, order.test.price or 0)


def ensure_bill_for_radiology(order_id):
    order = db.session.get(RadiologyOrder, order_id)
    if not order or not order.imaging_type:
        return None
    return _ensure_bill(order.patient_id, 'Radiology', order.id,
                        f'Radiology — {order.imaging_type.name}',
                        1, order.imaging_type.price or 0)


def ensure_bill_for_pharmacy(prescription_id):
    rx = db.session.get(Prescription, prescription_id)
    if not rx:
        return None
    total = 0.0
    # Bill only for the quantity actually dispensed (a partially dispensed item
    # whose remainder is never delivered must not be charged in full). Query the
    # dispensing records directly so freshly-flushed partial dispatches are
    # always reflected (instead of a possibly stale relationship collection).
    from app.models import DispensingRecord, PharmacyInventory
    for item in rx.items:
        dispensed = db.session.query(
            db.func.coalesce(db.func.sum(DispensingRecord.quantity), 0)
        ).filter(DispensingRecord.item_id == item.id, DispensingRecord.quantity != None).scalar() or 0
        if dispensed <= 0:
            continue
        inv = PharmacyInventory.query.filter_by(medication_id=item.medication_id).first()
        total += (inv.selling_price or 0) * dispensed
    if total <= 0:
        return None
    return _ensure_bill(rx.patient_id, 'Pharmacy', rx.id,
                        f'Medication dispense — Rx #{rx.id}',
                        1, total)


def ensure_bill_for_physio(session_id):
    """Generate a physiotherapy session bill once a session is completed."""
    from app.models import TherapySession
    session = db.session.get(TherapySession, session_id)
    if not session or session.status != 'Completed':
        return None
    amount = _service_price('Physiotherapy', 50.0)
    if amount <= 0:
        return None
    return _ensure_bill(session.patient_id, 'Physiotherapy', session.id,
                        f'Physiotherapy session (#{session.id})',
                        1, amount)


def ensure_bill_for_dental(procedure_id, cost=None):
    """Generate a dental procedure bill once the procedure is completed."""
    from app.models import DentalProcedure
    procedure = db.session.get(DentalProcedure, procedure_id)
    if not procedure or procedure.status != 'Completed':
        return None
    if cost is None:
        cost = procedure.cost or _service_price('Dentistry', 60.0)
    if cost <= 0:
        return None
    return _ensure_bill(procedure.patient_id, 'Dentistry', procedure.id,
                        f'Dental — {procedure.procedure_name or "Procedure"}',
                        1, cost)


def _service_price(category, default):
    from app.models import ServiceCatalog
    svc = ServiceCatalog.query.filter_by(category=category, is_active=True).first()
    if not svc:
        svc = ServiceCatalog.query.filter_by(category='General', is_active=True).first()
    if svc and svc.price:
        return svc.price
    return default


def _ensure_bill(patient_id, source_type, source_id, description, qty, price):
    existing = Bill.query.filter_by(source_type=source_type, source_id=source_id).first()
    if existing:
        return existing
    bill = Bill(patient_id=patient_id, source_type=source_type, source_id=source_id)
    db.session.add(bill)
    db.session.flush()
    db.session.add(BillItem(bill_id=bill.id, description=description,
                            quantity=qty, unit_price=price))
    db.session.flush()
    return bill
