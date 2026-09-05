"""Reusable patient-safety context for every clinical page.

Harvest: OpenMRS chart banner / FHIR PatientBanner (native reimplementation).
Any page that renders a specific patient for a clinician (charts, orders,
medication forms, AI tools, pharmacy, nursing, dentistry) can call
``patient_safety_context(patient_id)`` and splat the result into
``render_template``; the template then includes ``_patient_header.html`` /
``_patient_safety_strip.html`` so allergies, active problems and open alerts
are visible before any clinical action is taken.
"""

from app.models import (Allergy, Problem, ClinicalAlert, Prescription,
                        PrescriptionItem)


def _active_allergies(patient_id):
    return (Allergy.query
            .filter_by(patient_id=patient_id)
            .order_by(Allergy.created_at.desc()).all())


def _active_problems(patient_id):
    return (Problem.query
            .filter_by(patient_id=patient_id)
            .order_by(Problem.created_at.desc()).all())


def _open_alerts(patient_id):
    from app.services.alerts import open_alerts_for_patient
    return open_alerts_for_patient(patient_id)


def _active_meds(patient_id, limit=6):
    """Active prescription medications, deduplicated by medication."""
    items = (PrescriptionItem.query
             .join(Prescription, PrescriptionItem.prescription_id == Prescription.id)
             .filter(Prescription.patient_id == patient_id,
                     Prescription.status == 'Active',
                     PrescriptionItem.status != 'Cancelled')
             .order_by(Prescription.prescribed_date.desc())
             .all())
    seen = {}
    for item in items:
        med = item.medication
        if med is None:
            continue
        label = med.generic_name or med.brand_name or 'Medication'
        if label not in seen:
            seen[label] = item
    return list(seen.values())[:limit]


def patient_safety_context(patient_id):
    """Return a dict of safety data safe to splat into render_template."""
    return {
        'allergies': _active_allergies(patient_id),
        'problems': _active_problems(patient_id),
        'open_alerts': _open_alerts(patient_id),
        'active_meds': _active_meds(patient_id),
    }


def has_safety_concerns(safety_ctx):
    return bool(safety_ctx.get('allergies') or safety_ctx.get('problems')
                or safety_ctx.get('open_alerts'))