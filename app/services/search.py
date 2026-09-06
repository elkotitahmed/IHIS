"""Permission-aware global search service.

Used by the Ctrl+K search bar. The service only returns records whose patient
the acting user actually has a documented need-to-know relationship with; the
results themselves never leak other patients' identifiers.
"""
from sqlalchemy import or_

from flask_login import current_user

from app.access import accessible_patient_ids
from app.models import (
    Appointment, Doctor, LabOrder, Patient, PatientDocument, Prescription,
    RadiologyOrder, Referral, Task, User,
)


def global_search(query, limit=20):
    """Search across patients, staff, and clinical records.

    Returns a dict of search-result groups. Each value is a list of
    ``{'id', 'label', 'subtitle', 'extra'}`` dicts for template rendering.
    """
    q = (query or '').strip()
    if not q:
        return {}
    like = f'%{q}%'

    results = {}
    pids = accessible_patient_ids(current_user)
    has_patient_scope = bool(pids)

    if has_patient_scope:
        patients = (Patient.query
                    .join(User, Patient.user_id == User.id)
                    .filter(Patient.id.in_(pids),
                            or_(Patient.mrn.ilike(like),
                                Patient.phone.ilike(like),
                                Patient.address.ilike(like),
                                User.full_name.ilike(like),
                                User.email.ilike(like)))
                    .limit(limit).all())
    else:
        patients = []
    results['patients'] = [
        {'id': p.id, 'label': full_name(p), 'subtitle': (p.mrn or 'No MRN'),
         'extra': p.gender or ''} for p in patients]

    doctors = (Doctor.query.join(User, Doctor.user_id == User.id)
               .filter(User.full_name.ilike(like))
               .limit(limit).all())
    results['doctors'] = [
        {'id': d.id, 'label': (d.user.full_name if d.user else f'Physician #{d.id}'),
         'subtitle': d.specialty.name if d.specialty else 'No specialty', 'extra': ''}
        for d in doctors]

    scoped = {
        'appointments': Appointment,
        'lab_orders': LabOrder,
        'radiology_orders': RadiologyOrder,
        'prescriptions': Prescription,
        'referrals': Referral,
        'documents': PatientDocument,
        'tasks': Task,
    }
    for key, Model in scoped.items():
        if has_patient_scope:
            results[key] = record_rows(Model, Model.patient_id.in_(pids),
                                       like, label_for(Model, key), key, limit)
        else:
            results[key] = []

    return {k: v for k, v in results.items() if v}


def full_name(patient):
    return patient.user.full_name if patient.user else f'Patient #{patient.id}'


def label_for(Model, kind):
    """Return a per-kind label builder for a record row."""
    def build(r):
        try:
            if kind == 'lab_orders' and getattr(r, 'test', None):
                return f"Lab: {r.test.test_name}"
            if kind == 'radiology_orders' and getattr(r, 'imaging_type', None):
                return f"Imaging: {r.imaging_type.name}"
            if kind == 'documents':
                return f"Doc: {r.title or r.document_type or 'Untitled'}"
            if kind == 'tasks':
                return f"Task: {r.title}"
            if kind == 'referrals':
                return f"Referral {r.to_specialty or '-'} (#{r.id})"
            return f'{kind.replace("_", " ").title()} #{r.id}'
        except Exception:
            return f'{kind.replace("_", " ").title()} #{r.id}'
    return build


_SKIP_COLS = {'status', 'priority', 'image_urls', 'barcode',
              'accession_number', 'result_value', 'result_unit'}


def record_rows(Model, filter_clause, like, label_fn, kind, limit):
    """Generic record search against a model's free-text columns."""
    text_cols = [c for c in Model.__table__.columns
                 if c.type.python_type is str and c.name not in _SKIP_COLS]
    if not text_cols:
        return []
    or_clause = or_(*[col.ilike(like) for col in text_cols])
    rows = Model.query.filter(filter_clause, or_clause).limit(limit).all()
    return [{'id': r.id, 'label': label_fn(r),
             'subtitle': f'{kind.replace("_", " ").title()} #{r.id}',
             'extra': getattr(r, 'status', None) or getattr(r, 'category', None) or '',
             'patient_id': getattr(r, 'patient_id', None)} for r in rows]