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

class _DerivedEvent:
    """Read-only stand-in for a TimelineEvent synthesised from a source
    record that was written before the timeline engine existed (or by a bulk
    import). Mirrors the attributes the templates use."""
    __slots__ = ('patient_id', 'event_type', 'title', 'description',
                 'occurred_at', 'source_type', 'source_id', 'department',
                 'created_by', 'derived')

    def __init__(self, patient_id, event_type, title, description, occurred_at,
                 source_type, source_id, department):
        self.patient_id = patient_id
        self.event_type = event_type
        self.title = title
        self.description = description
        self.occurred_at = occurred_at
        self.source_type = source_type
        self.source_id = source_id
        self.department = department
        self.created_by = None
        self.derived = True


def merged_timeline(patient_id, limit=120):
    """Every important patient event in one ordered list.

    Persisted TimelineEvent rows come first; any source record (appointment,
    encounter, diagnosis, order, result, prescription, dispense, admission,
    discharge, referral, therapy, dental, bill, payment, follow-up,
    immunization, document) that has *no* persisted event is represented by a
    derived entry so nothing is orphaned from the story. Deduplicated on
    (source_type, source_id)."""
    from app.models import (Appointment, MedicalRecord, Diagnosis, LabOrder,
                            RadiologyOrder, Prescription, DispensingRecord,
                            Admission, Referral, TherapySession, DentalProcedure,
                            Bill, Payment, FollowUp, ImmunizationRecord,
                            PatientDocument, VitalSign, NursingNote)
    persisted = (TimelineEvent.query.filter_by(patient_id=patient_id)
                 .order_by(TimelineEvent.occurred_at.desc()).limit(limit).all())
    seen = {(e.source_type, e.source_id) for e in persisted if e.source_type and e.source_id}
    events = list(persisted)

    def add(stype, sid, etype, title, desc, when, dept):
        if when is None or (stype, sid) in seen:
            return
        seen.add((stype, sid))
        events.append(_DerivedEvent(patient_id, etype, title, desc, when, stype, sid, dept))

    for a in Appointment.query.filter_by(patient_id=patient_id).all():
        who = a.doctor.user.full_name if a.doctor and a.doctor.user else 'doctor'
        add('appointment', a.id, 'APPOINTMENT', f'Appointment ({a.status})',
            f'With Dr. {who}' + (f' · {a.reason}' if a.reason else ''), a.scheduled_at, 'Reception')
    for r in MedicalRecord.query.filter_by(patient_id=patient_id).all():
        add('medical_record', r.id, 'VISIT', f'Consultation: {r.diagnosis or "clinical note"}',
            (r.treatment_plan or r.clinical_notes or '')[:160], r.visit_date, 'Doctor')
    for d in Diagnosis.query.filter_by(patient_id=patient_id).all():
        add('diagnosis', d.id, 'DIAGNOSIS', f'Diagnosis: {d.description}',
            f'ICD-10 {d.icd10_code}' if d.icd10_code else None, d.date_diagnosed, 'Doctor')
    for o in LabOrder.query.filter_by(patient_id=patient_id).all():
        name = o.test.test_name if o.test else 'Lab test'
        add('lab_order', o.id, 'LAB', f'Lab order: {name}', f'Status {o.status}', o.order_date, 'Laboratory')
        if o.result and o.result.result_date:
            add('lab_result', o.result.id, 'LAB', f'Lab result: {name}',
                f'{o.result.result_value or ""} {o.result.result_unit or ""}'
                + (' · CRITICAL' if o.result.is_critical else ' · abnormal' if o.result.is_abnormal else ''),
                o.result.result_date, 'Laboratory')
    for o in RadiologyOrder.query.filter_by(patient_id=patient_id).all():
        name = o.imaging_type.name if o.imaging_type else 'Imaging'
        add('radiology_order', o.id, 'RADIOLOGY', f'Imaging ordered: {name}', f'Status {o.status}',
            o.order_date, 'Radiology')
        if o.report and o.report.report_date:
            add('radiology_report', o.report.id, 'RADIOLOGY', f'Report: {name}',
                (o.report.impression or '')[:160], o.report.report_date, 'Radiology')
    for rx in Prescription.query.filter_by(patient_id=patient_id).all():
        meds = ', '.join(i.medication.generic_name for i in rx.items if i.medication)
        add('prescription', rx.id, 'PRESCRIPTION', f'Prescription #{rx.id}',
            meds or f'{len(rx.items)} item(s)', rx.prescribed_date, 'Doctor')
    for dr in DispensingRecord.query.filter_by(prescription_id=None).all():
        pass  # dispensing records always carry a prescription
    for dr in (DispensingRecord.query.join(Prescription, DispensingRecord.prescription_id == Prescription.id)
               .filter(Prescription.patient_id == patient_id).all()):
        add('dispensing_record', dr.id, 'DISPENSE', f'Dispensed for Rx #{dr.prescription_id}',
            f'{dr.quantity or 0} unit(s)', dr.dispensed_at, 'Pharmacy')
    for ad in Admission.query.filter_by(patient_id=patient_id).all():
        add('admission', ad.id, 'ADMISSION', f'Admitted ({ad.admission_no})',
            f'{ad.ward.name if ad.ward else "Ward"}' + (f' · {ad.reason}' if ad.reason else ''),
            ad.admitted_at, 'Admissions')
        if ad.discharged_at:
            add('discharge', ad.id, 'DISCHARGE', f'Discharged ({ad.admission_no})',
                (ad.discharge_diagnosis or ad.discharge_summary or '')[:160], ad.discharged_at, 'Admissions')
    for ref in Referral.query.filter_by(patient_id=patient_id).all():
        add('referral', ref.id, 'REFERRAL', f'Referral to {ref.to_specialty or "specialist"} ({ref.status})',
            ref.reason, ref.created_at, 'Care Coordination')
    for ts in TherapySession.query.filter_by(patient_id=patient_id).all():
        add('therapy_session', ts.id, 'PHYSIOTHERAPY', f'Therapy session ({ts.status})',
            ts.session_type, ts.scheduled_at, 'Rehabilitation')
    for dp in DentalProcedure.query.filter_by(patient_id=patient_id).all():
        add('dental_procedure', dp.id, 'DENTISTRY', f'Dental: {dp.procedure_name} ({dp.status})',
            f'Tooth {dp.tooth_number}' if dp.tooth_number else None, dp.performed_at, 'Dentistry')
    for b in Bill.query.filter_by(patient_id=patient_id).all():
        add('bill', b.id, 'BILLING', f'Bill {b.bill_no or "#" + str(b.id)} ({b.status})',
            f'{b.source_type or "Manual"} · {b.total():.2f}', b.issued_at, 'Billing')
        for pmt in b.payments:
            add('payment', pmt.id, 'PAYMENT', f'Payment {pmt.receipt_no or ""}'.strip(),
                f'{pmt.amount:.2f} via {pmt.method}', pmt.received_at, 'Billing')
    for fu in FollowUp.query.filter_by(patient_id=patient_id).all():
        add('follow_up', fu.id, 'FOLLOW_UP', f'Follow-up ({fu.status})', fu.reason, fu.scheduled_for, 'Clinical')
    for im in ImmunizationRecord.query.filter_by(patient_id=patient_id).all():
        add('immunization', im.id, 'IMMUNIZATION', f'Immunization: {im.vaccine_name}',
            f'Dose {im.dose_number}', im.administered_at, 'Clinical')
    for doc in PatientDocument.query.filter_by(patient_id=patient_id).all():
        add('patient_document', doc.id, 'DOCUMENT', f'Document: {doc.title or doc.document_type}',
            doc.category, doc.uploaded_at, 'Documents')
    for v in VitalSign.query.filter_by(patient_id=patient_id).all():
        add('vital_sign', v.id, 'VITALS', 'Vital signs recorded',
            f'BP {v.blood_pressure_systolic}/{v.blood_pressure_diastolic} · HR {v.heart_rate} · '
            f'Temp {v.temperature} · SpO2 {v.oxygen_saturation}', v.recorded_at, 'Nursing')
    for n in NursingNote.query.filter_by(patient_id=patient_id).all():
        add('nursing_note', n.id, 'NURSING', 'Nursing note', (n.note or '')[:160], n.created_at, 'Nursing')

    events.sort(key=lambda e: e.occurred_at, reverse=True)
    return events[:limit]
