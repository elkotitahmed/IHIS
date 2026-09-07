"""Deterministic, internationally standard clinical scores.

* NEWS2  – National Early Warning Score 2 (Royal College of Physicians, 2017)
* qSOFA  – quick Sequential Organ Failure Assessment (Sepsis-3, 2016)
* LACE   – 30-day readmission risk index (van Walraven et al., 2010)

Pure functions over the data iHIS already stores; no AI, fully explainable.
Missing inputs never raise: the component is reported as missing and the
score is flagged as partial so nobody trusts a number built on gaps.
"""
import re
from datetime import timedelta


# --------------------------------------------------------------------------- NEWS2
def _band(value, bands):
    """bands: list of (upper_inclusive, points); the last upper may be None."""
    for upper, pts in bands:
        if upper is None or value <= upper:
            return pts
    return bands[-1][1]


def news2(vital, on_oxygen=False, consciousness='A', hypercapnic=False):
    """Return {'score', 'band', 'components', 'missing', 'partial', 'advice'}.

    ``consciousness``: 'A' (alert) or any of 'C','V','P','U' (new confusion,
    voice, pain, unresponsive) which all score 3.  ``hypercapnic`` selects the
    SpO2 scale 2 (target 88–92 %) used for known type-2 respiratory failure.
    """
    comp, missing = {}, []
    rr = getattr(vital, 'respiratory_rate', None)
    if rr is None:
        missing.append('respiratory_rate')
    else:
        comp['respiratory_rate'] = _band(rr, [(8, 3), (11, 1), (20, 0), (24, 2), (None, 3)])
    spo2 = getattr(vital, 'oxygen_saturation', None)
    if spo2 is None:
        missing.append('oxygen_saturation')
    elif hypercapnic:
        if on_oxygen:
            comp['oxygen_saturation'] = _band(spo2, [(83, 3), (85, 2), (87, 1), (92, 0), (94, 1), (96, 2), (None, 3)])
        else:
            comp['oxygen_saturation'] = _band(spo2, [(83, 3), (85, 2), (87, 1), (None, 0)])
    else:
        comp['oxygen_saturation'] = _band(spo2, [(91, 3), (93, 2), (95, 1), (None, 0)])
    comp['supplemental_oxygen'] = 2 if on_oxygen else 0
    sbp = getattr(vital, 'blood_pressure_systolic', None)
    if sbp is None:
        missing.append('blood_pressure_systolic')
    else:
        comp['blood_pressure_systolic'] = _band(sbp, [(90, 3), (100, 2), (110, 1), (219, 0), (None, 3)])
    hr = getattr(vital, 'heart_rate', None)
    if hr is None:
        missing.append('heart_rate')
    else:
        comp['heart_rate'] = _band(hr, [(40, 3), (50, 1), (90, 0), (110, 1), (130, 2), (None, 3)])
    comp['consciousness'] = 0 if (consciousness or 'A').upper().startswith('A') else 3
    temp = getattr(vital, 'temperature', None)
    if temp is None:
        missing.append('temperature')
    else:
        comp['temperature'] = _band(round(float(temp), 1), [(35.0, 3), (36.0, 1), (38.0, 0), (39.0, 1), (None, 2)])

    score = sum(comp.values())
    any_three = any(v == 3 for k, v in comp.items() if k != 'supplemental_oxygen')
    if score >= 7:
        band, advice = 'HIGH', 'Emergency response: urgent clinical review by a team with critical-care skills; continuous monitoring.'
    elif score >= 5 or any_three:
        band, advice = ('MEDIUM' if score >= 5 else 'LOW-MEDIUM',
                        'Urgent review by a clinician; monitor at least hourly.')
    else:
        band, advice = 'LOW', 'Routine monitoring (every 4–12 h).'
    return {'score': score, 'band': band, 'components': comp, 'missing': missing,
            'partial': bool(missing), 'advice': advice}


# --------------------------------------------------------------------------- qSOFA
def qsofa(vital, altered_mentation=False):
    """0–3. Two or more points = higher risk of poor outcome with suspected infection."""
    pts, missing = {}, []
    rr = getattr(vital, 'respiratory_rate', None)
    sbp = getattr(vital, 'blood_pressure_systolic', None)
    if rr is None:
        missing.append('respiratory_rate')
    else:
        pts['respiratory_rate'] = 1 if rr >= 22 else 0
    if sbp is None:
        missing.append('blood_pressure_systolic')
    else:
        pts['blood_pressure_systolic'] = 1 if sbp <= 100 else 0
    pts['altered_mentation'] = 1 if altered_mentation else 0
    score = sum(pts.values())
    return {'score': score, 'positive': score >= 2, 'components': pts, 'missing': missing,
            'partial': bool(missing),
            'advice': 'qSOFA ≥ 2: assess for sepsis (lactate, cultures, early antibiotics per protocol).' if score >= 2 else ''}


# --------------------------------------------------------------------------- LACE
_COMORBIDITY_POINTS = (
    # (keywords, Charlson-style points)
    (('metastatic', 'metastasis'), 6),
    (('aids', 'hiv'), 6),
    (('lymphoma', 'leukemia', 'leukaemia', 'myeloma', 'cancer', 'carcinoma', 'tumor', 'tumour', 'malignan'), 2),
    (('cirrhosis', 'liver failure', 'hepatic failure'), 3),
    (('diabetes with', 'nephropathy', 'retinopathy', 'neuropathy'), 2),
    (('diabetes', 'dm'), 1),
    (('heart failure', 'chf', 'cardiac failure'), 1),
    (('myocardial infarction', 'mi ', 'ischemic heart', 'ischaemic heart', 'coronary'), 1),
    (('copd', 'chronic obstructive', 'emphysema', 'asthma'), 1),
    (('renal', 'kidney disease', 'ckd', 'dialysis'), 2),
    (('stroke', 'cerebrovascular', 'tia', 'hemiplegia', 'paraplegia'), 2),
    (('dementia', 'alzheimer'), 3),
    (('peripheral vascular', 'pvd'), 1),
    (('ulcer', 'peptic'), 1),
    (('rheumat', 'lupus', 'connective tissue'), 1),
)


def comorbidity_points(text):
    """Approximate Charlson points from free-text conditions / diagnoses."""
    t = ' ' + re.sub(r'[^a-z0-9 ]+', ' ', (text or '').lower()) + ' '
    pts, used = 0, set()
    for keys, p in _COMORBIDITY_POINTS:
        if any(re.search(r'\b' + re.escape(k.strip()) + r'\b', t) for k in keys):
            if p in (1, 2) and 'diabetes' in keys[0] and 'diabetes' in used:
                continue
            pts += p
            used.add(keys[0])
    return min(pts, 5)


def lace(length_of_stay_days, acute_admission, comorbidity_pts, ed_visits_6m):
    """Return {'score', 'risk', 'components'}; ≥10 = high readmission risk."""
    los = length_of_stay_days if length_of_stay_days is not None else 0
    l_pts = 0 if los < 1 else 1 if los < 2 else 2 if los < 3 else 3 if los < 4 else 4 if los <= 6 else 5 if los <= 13 else 7
    a_pts = 3 if acute_admission else 0
    c_pts = min(int(comorbidity_pts or 0), 5)
    e_pts = min(int(ed_visits_6m or 0), 4)
    score = l_pts + a_pts + c_pts + e_pts
    risk = 'HIGH' if score >= 10 else 'MODERATE' if score >= 5 else 'LOW'
    return {'score': score, 'risk': risk,
            'components': {'length_of_stay': l_pts, 'acute_admission': a_pts,
                           'comorbidity': c_pts, 'ed_visits_6m': e_pts},
            'advice': {'HIGH': 'Arrange follow-up within 7 days, medication reconciliation and a discharge call.',
                       'MODERATE': 'Book follow-up within 14 days and confirm the patient understands the plan.',
                       'LOW': 'Standard discharge instructions.'}[risk]}


def lace_for_admission(admission, now=None):
    """LACE from an Admission row (patient text + appointment history)."""
    from datetime import datetime
    from app.models import Appointment
    now = now or datetime.utcnow()
    end = admission.discharged_at or now
    los = max((end - admission.admitted_at).days, 0) if admission.admitted_at else 0
    reason = (admission.reason or '').lower()
    acute = any(k in reason for k in ('emergency', 'acute', 'urgent', 'er ', 'ed ')) or \
        (getattr(admission, 'admission_type', '') or '').lower() in ('emergency', 'urgent')
    patient = admission.patient
    text = ' '.join(filter(None, [
        getattr(patient, 'chronic_diseases', '') or '',
        ' '.join((d.description or '') for d in getattr(patient, 'diagnoses', []) or []),
        admission.discharge_diagnosis or '', admission.reason or '']))
    cpts = comorbidity_points(text)
    since = now - timedelta(days=182)
    ed = Appointment.query.filter(
        Appointment.patient_id == admission.patient_id,
        Appointment.scheduled_at >= since,
        (Appointment.priority.in_(['Urgent', 'Emergency']) | (Appointment.visit_type == 'WalkIn'))).count()
    out = lace(los, acute, cpts, ed)
    out['inputs'] = {'length_of_stay_days': los, 'acute_admission': acute,
                     'comorbidity_points': cpts, 'ed_visits_6m': ed}
    return out
