"""Data-minimised clinical context for AI features.

Everything the Copilot, the result reviewer and the patient assistant show
or send to a model is built here from *verified* records. The context never
contains the patient's name, MRN, phone or address — only clinical facts —
and every list is bounded so prompts stay small (free-tier friendly).
"""
from datetime import date, timedelta

from app.models import (Admission, Allergy, Appointment, Diagnosis, FollowUp, LabOrder,
                        LabResult, MedicalRecord, MedicationReconciliation, NursingNote,
                        Prescription, PrescriptionItem, Problem, RadiologyOrder, Referral,
                        Task, VitalSign)
from app.services.ai.platform import sanitize_text
from app.utils import utcnow

# Deterministic red-flag thresholds (authoritative; AI only explains them).
VITAL_RULES = (
    ('oxygen_saturation', lambda v: v < 92, 'SpO2 below 92%'),
    ('blood_pressure_systolic', lambda v: v < 90, 'Systolic BP below 90'),
    ('blood_pressure_systolic', lambda v: v > 180, 'Systolic BP above 180'),
    ('heart_rate', lambda v: v > 120, 'Heart rate above 120'),
    ('heart_rate', lambda v: v < 45, 'Heart rate below 45'),
    ('respiratory_rate', lambda v: v > 28, 'Respiratory rate above 28'),
    ('temperature', lambda v: v >= 39.0, 'Temperature 39 °C or higher'),
    ('temperature', lambda v: v < 35.0, 'Temperature below 35 °C'),
    ('pain_score', lambda v: v >= 8, 'Severe pain (8/10 or more)'),
    ('blood_glucose', lambda v: v < 60, 'Hypoglycaemia (glucose < 60)'),
    ('blood_glucose', lambda v: v > 350, 'Marked hyperglycaemia (glucose > 350)'),
)


def _d(value):
    return value.strftime('%Y-%m-%d') if value else None


def _age(patient):
    if not patient.date_of_birth:
        return None
    t = date.today()
    return t.year - patient.date_of_birth.year - (
        (t.month, t.day) < (patient.date_of_birth.month, patient.date_of_birth.day))


def vital_flags(v):
    """Deterministic red flags for one VitalSign row."""
    flags = []
    for field, test, label in VITAL_RULES:
        val = getattr(v, field, None)
        if val is not None:
            try:
                if test(float(val)):
                    flags.append(label)
            except (TypeError, ValueError):
                continue
    return flags


def build_context(patient, *, labs=12, imaging=6, encounters=5, days=365):
    """Return a JSON-serialisable dict of verified clinical facts."""
    pid = patient.id
    since = utcnow() - timedelta(days=days)

    problems = [{'description': sanitize_text(p.description, 200), 'icd10': p.icd10_code,
                 'status': p.status, 'severity': p.severity, 'onset': _d(p.onset) if hasattr(p.onset, 'strftime') else p.onset}
                for p in Problem.query.filter_by(patient_id=pid)
                .order_by(Problem.created_at.desc()).limit(15).all()]
    allergies = [{'substance': sanitize_text(a.substance, 100), 'reaction': sanitize_text(a.reaction, 100),
                  'severity': a.severity, 'status': a.status}
                 for a in Allergy.query.filter_by(patient_id=pid).limit(15).all()]
    if patient.allergies and not allergies:
        allergies.append({'substance': sanitize_text(patient.allergies, 200), 'reaction': None,
                          'severity': None, 'status': 'free-text'})

    meds = []
    seen = set()
    items = (PrescriptionItem.query.join(Prescription, PrescriptionItem.prescription_id == Prescription.id)
             .filter(Prescription.patient_id == pid, Prescription.status == 'Active',
                     PrescriptionItem.status != 'Cancelled')
             .order_by(Prescription.prescribed_date.desc()).limit(40).all())
    for it in items:
        m = it.medication
        if not m or m.id in seen:
            continue
        seen.add(m.id)
        meds.append({'id': m.id, 'name': m.generic_name, 'brand': m.brand_name,
                     'dosage': sanitize_text(it.dosage, 60), 'frequency': sanitize_text(it.frequency, 60),
                     'duration': sanitize_text(it.duration, 60), 'since': _d(it.prescription.prescribed_date),
                     'prescription_id': it.prescription_id})

    lab_rows = []
    for o in (LabOrder.query.filter_by(patient_id=pid).order_by(LabOrder.order_date.desc()).limit(60).all()):
        r = o.result
        name = o.test.test_name if o.test else 'Lab test'
        if r and r.result_value is not None:
            lab_rows.append({'order_id': o.id, 'result_id': r.id, 'test': name,
                             'value': sanitize_text(r.result_value, 40),
                             'unit': r.result_unit or (o.test.unit if o.test else None),
                             'reference': o.test.normal_range if o.test else None,
                             'abnormal': bool(r.is_abnormal), 'critical': bool(r.is_critical),
                             'status': r.status, 'date': _d(r.result_date or o.order_date)})
        if len(lab_rows) >= labs:
            break
    pending_labs = [{'order_id': o.id, 'test': o.test.test_name if o.test else 'Lab test',
                     'status': o.status, 'ordered': _d(o.order_date)}
                    for o in LabOrder.query.filter(LabOrder.patient_id == pid,
                                                   LabOrder.status.in_(('Pending', 'Accepted', 'Collected',
                                                                        'ReceivedAtLab', 'Processing', 'Resulted')))
                    .order_by(LabOrder.order_date.desc()).limit(10).all()]

    imaging_rows, pending_imaging = [], []
    for o in RadiologyOrder.query.filter_by(patient_id=pid).order_by(RadiologyOrder.order_date.desc()).limit(20).all():
        name = o.imaging_type.name if o.imaging_type else 'Imaging'
        rep = o.report
        if rep and (rep.impression or rep.findings):
            if len(imaging_rows) < imaging:
                imaging_rows.append({'order_id': o.id, 'report_id': rep.id, 'study': name,
                                     'impression': sanitize_text(rep.impression, 400),
                                     'findings': sanitize_text(rep.findings, 600),
                                     'status': rep.status, 'date': _d(rep.report_date or o.order_date)})
        elif o.status not in ('Cancelled', 'Finalized', 'Signed'):
            pending_imaging.append({'order_id': o.id, 'study': name, 'status': o.status,
                                    'ordered': _d(o.order_date)})

    enc = [{'record_id': r.id, 'date': _d(r.visit_date), 'diagnosis': sanitize_text(r.diagnosis, 200),
            'notes': sanitize_text(r.clinical_notes, 500), 'plan': sanitize_text(r.treatment_plan, 400),
            'status': r.status}
           for r in MedicalRecord.query.filter_by(patient_id=pid)
           .order_by(MedicalRecord.visit_date.desc()).limit(encounters).all()]
    diagnoses = [{'code': d.icd10_code, 'description': sanitize_text(d.description, 200),
                  'date': _d(d.date_diagnosed), 'primary': bool(d.is_primary)}
                 for d in Diagnosis.query.filter_by(patient_id=pid)
                 .order_by(Diagnosis.date_diagnosed.desc()).limit(10).all()]

    vitals = []
    for v in VitalSign.query.filter_by(patient_id=pid).order_by(VitalSign.recorded_at.desc()).limit(3).all():
        vitals.append({'date': _d(v.recorded_at), 'temp': v.temperature,
                       'bp': (f'{v.blood_pressure_systolic}/{v.blood_pressure_diastolic}'
                              if v.blood_pressure_systolic else None),
                       'hr': v.heart_rate, 'rr': v.respiratory_rate, 'spo2': v.oxygen_saturation,
                       'pain': v.pain_score, 'glucose': v.blood_glucose,
                       'weight_kg': v.weight_kg, 'flags': vital_flags(v)})

    tasks = [{'id': t.id, 'title': sanitize_text(t.title, 120), 'status': t.status,
              'priority': t.priority, 'due': _d(t.due_at), 'department': t.department}
             for t in Task.query.filter(Task.patient_id == pid,
                                        Task.status.in_(('NEW', 'ASSIGNED', 'IN_PROGRESS')))
             .order_by(Task.due_at.asc().nulls_last()).limit(10).all()]
    follow_ups = [{'id': f.id, 'when': _d(f.scheduled_for), 'reason': sanitize_text(f.reason, 150),
                   'status': f.status}
                  for f in FollowUp.query.filter(FollowUp.patient_id == pid,
                                                 FollowUp.status.in_(('Scheduled', 'Pending', 'Overdue')))
                  .order_by(FollowUp.scheduled_for.asc()).limit(5).all()]
    appts = [{'id': a.id, 'when': a.scheduled_at.strftime('%Y-%m-%d %H:%M') if a.scheduled_at else None,
              'status': a.status, 'reason': sanitize_text(a.reason, 200), 'visit_type': a.visit_type}
             for a in Appointment.query.filter_by(patient_id=pid)
             .order_by(Appointment.scheduled_at.desc()).limit(5).all()]
    admissions = [{'id': ad.id, 'admitted': _d(ad.admitted_at), 'discharged': _d(ad.discharged_at),
                   'status': ad.status, 'reason': sanitize_text(ad.reason, 200),
                   'ward': ad.ward.name if ad.ward else None,
                   'discharge_diagnosis': sanitize_text(ad.discharge_diagnosis, 200)}
                  for ad in Admission.query.filter_by(patient_id=pid)
                  .order_by(Admission.admitted_at.desc()).limit(3).all()]
    referrals = [{'id': r.id, 'to': r.to_specialty, 'status': r.status, 'reason': sanitize_text(r.reason, 150),
                  'date': _d(r.created_at)}
                 for r in Referral.query.filter_by(patient_id=pid)
                 .order_by(Referral.created_at.desc()).limit(5).all()]
    nursing = [{'date': _d(n.created_at), 'note': sanitize_text(n.note, 300)}
               for n in NursingNote.query.filter_by(patient_id=pid)
               .order_by(NursingNote.created_at.desc()).limit(3).all()]
    recon = None
    last_recon = (MedicationReconciliation.query.filter_by(patient_id=pid)
                  .order_by(MedicationReconciliation.created_at.desc()).first())
    if last_recon:
        recon = {'id': last_recon.id, 'date': _d(last_recon.created_at), 'status': last_recon.status,
                 'type': last_recon.reconciliation_type,
                 'discrepancies': [{'type': d.discrepancy_type, 'severity': d.severity,
                                    'description': sanitize_text(d.description, 200),
                                    'action': sanitize_text(d.recommended_action, 150), 'status': d.status}
                                   for d in last_recon.discrepancies[:10]]
                 if hasattr(last_recon, 'discrepancies') else []}

    current_admission = next((a for a in admissions if a['status'] == 'Admitted'), None)
    why_here = None
    for a in appts:
        if a['status'] in ('CheckedIn', 'InConsultation', 'Scheduled', 'Confirmed') and a['reason']:
            why_here = a['reason']
            break
    if not why_here and current_admission:
        why_here = current_admission['reason']
    if not why_here and enc:
        why_here = enc[0]['diagnosis'] or enc[0]['notes']

    return {
        'demographics': {'age': _age(patient), 'gender': patient.gender, 'blood_type': patient.blood_type,
                         'chronic': sanitize_text(patient.chronic_diseases, 300)},
        'why_here': why_here, 'problems': problems, 'allergies': allergies, 'medications': meds,
        'labs': lab_rows, 'pending_labs': pending_labs, 'imaging': imaging_rows,
        'pending_imaging': pending_imaging, 'encounters': enc, 'diagnoses': diagnoses,
        'vitals': vitals, 'tasks': tasks, 'follow_ups': follow_ups, 'appointments': appts,
        'admissions': admissions, 'current_admission': current_admission, 'referrals': referrals,
        'nursing_notes': nursing, 'reconciliation': recon,
        'generated_at': utcnow().strftime('%Y-%m-%d %H:%M'),
    }


def context_fingerprint(ctx):
    """A compact, content-derived key used for caching: any change to the
    verified data changes the fingerprint (so cached AI never goes stale
    against the chart)."""
    keys = ('why_here', 'problems', 'allergies', 'medications', 'labs', 'pending_labs',
            'imaging', 'pending_imaging', 'encounters', 'diagnoses', 'vitals', 'tasks',
            'follow_ups', 'appointments', 'admissions', 'referrals', 'nursing_notes',
            'reconciliation')
    return {k: ctx.get(k) for k in keys}
