"""Explainable, deterministic health-risk profile for one patient.

Replaces the old heuristic (age + free-text allergies + "chronic disease
present") with a transparent score assembled from the engines the rest of iHIS
already trusts:

* age and comorbidity burden (Charlson-style points from the problem list)
* renal function (Cockcroft-Gault / eGFR from ``medication_review``)
* latest vital signs through NEWS2
* laboratory: recent abnormal and critical results
* medication safety findings (``medication_review.review_patient``),
  polypharmacy and high-risk drug classes
* open clinical alerts, current / recent admissions
* *documented drug allergies* — "NKDA" is recognised and never counted

Every factor carries its points and the evidence behind it, and every
observation names its source, so a clinician (or a patient reading their own
record) can see exactly why the number is what it is. No language model is
involved; an AI narrative is a separate, explicit request.
"""
from datetime import date, datetime, timedelta

from app import db
from app.models import (Admission, Allergy, ClinicalAlert, Diagnosis, FollowUp, LabOrder, LabResult,
                        LabTestCatalog, MedicalRecord, Prescription, PrescriptionItem, Problem, VitalSign)

NKDA_MARKERS = ('nkda', 'no known', 'none', 'nil', 'لا يوجد', 'لا توجد')
HIGH_RISK_CLASSES = {
    'anticoagulant': {'warfarin', 'apixaban', 'rivaroxaban', 'dabigatran', 'edoxaban', 'enoxaparin', 'heparin'},
    'insulin': {'insulin glargine', 'insulin', 'insulin aspart', 'insulin lispro'},
    'opioid': {'morphine', 'oxycodone', 'fentanyl', 'tramadol', 'codeine', 'hydromorphone'},
    'digoxin': {'digoxin'},
    'immunosuppressant': {'methotrexate', 'azathioprine', 'cyclosporine', 'tacrolimus', 'mycophenolate'},
    'antiarrhythmic': {'amiodarone', 'sotalol', 'flecainide'},
}
SEV_RANK = {'critical': 4, 'high': 3, 'moderate': 2, 'low': 1, 'info': 0}


def _age(patient, today=None):
    if not patient.date_of_birth:
        return None
    today = today or date.today()
    d = patient.date_of_birth
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def documented_allergies(patient):
    """Structured active allergies + a free-text entry only when it is a real
    allergy (NKDA / none are not allergies)."""
    out = [a for a in Allergy.query.filter_by(patient_id=patient.id).all() if (a.status or 'Active') != 'Inactive']
    free = (patient.allergies or '').strip()
    if free and not any(m in free.lower() for m in NKDA_MARKERS) and not out:
        out.append(type('FreeTextAllergy', (), {'substance': free, 'reaction': None, 'severity': None, 'free_text': True})())
    return out


def _factor(label, points, evidence, source):
    return {'label': label, 'points': points, 'evidence': evidence, 'source': source}


def _obs(severity, title, detail, source, url=None):
    return {'severity': severity, 'title': title, 'detail': detail, 'source': source, 'url': url}


def build(patient, now=None):
    from app.services import medication_review as mr
    from app.services.clinical_scores import comorbidity_points, news2
    now = now or datetime.utcnow()
    factors, obs, missing = [], [], []

    # ---- age -------------------------------------------------------------
    age = _age(patient, now.date())
    if age is None:
        missing.append('date of birth')
    elif age >= 75:
        factors.append(_factor('Age 75 or over', 3, f'{age} years', 'Chart'))
    elif age >= 65:
        factors.append(_factor('Age 65–74', 2, f'{age} years', 'Chart'))
    elif age <= 2:
        factors.append(_factor('Infant / toddler', 2, f'{age} years', 'Chart'))

    # ---- problems & comorbidity ------------------------------------------
    problems = [p for p in Problem.query.filter_by(patient_id=patient.id).all() if (p.status or 'Active') == 'Active']
    diagnoses = Diagnosis.query.filter_by(patient_id=patient.id).order_by(Diagnosis.date_diagnosed.desc()).all()
    # Family history / exposure entries (ICD-10 Z-codes) are context, not comorbidity.
    def _is_condition(code, desc):
        d = (desc or '').lower()
        return not ((code or '').upper().startswith('Z') or 'family history' in d or d.startswith('history of'))
    cond_text = ' ; '.join([p.description for p in problems if _is_condition(p.icd10_code, p.description)]
                           + [d.description for d in diagnoses if _is_condition(d.icd10_code, d.description)]
                           + [patient.chronic_diseases or ''])
    cpts = comorbidity_points(cond_text) if cond_text.strip(' ;') else 0
    if cpts:
        pts = min(int(cpts), 4)
        factors.append(_factor('Comorbidity burden', pts,
                               f'Charlson-style {cpts} point(s) from {len(problems)} active problem(s)', 'Problem list'))
    if problems:
        obs.append(_obs('info', f'{len(problems)} active problem(s) on the list',
                        ' · '.join(f'{p.description} ({p.icd10_code})' if p.icd10_code else p.description for p in problems[:6]),
                        'Problem list'))

    # ---- renal function --------------------------------------------------
    renal = mr.renal_function(patient)
    eff = renal.get('effective')
    if eff is not None:
        if eff < 30:
            factors.append(_factor('Severe renal impairment', 3, f'CrCl/eGFR {eff:.0f} mL/min ({renal["stage"]})', renal['method']))
        elif eff < 45:
            factors.append(_factor('Moderate–severe renal impairment', 2, f'CrCl/eGFR {eff:.0f} mL/min ({renal["stage"]})', renal['method']))
        elif eff < 60:
            factors.append(_factor('Mild–moderate renal impairment', 1, f'CrCl/eGFR {eff:.0f} mL/min ({renal["stage"]})', renal['method']))
        if eff < 60:
            obs.append(_obs('high' if eff < 45 else 'moderate', 'Reduced kidney function',
                            f'SCr {renal["scr"]} {renal.get("scr_unit") or ""} · CrCl {renal["crcl"]} mL/min ({renal["method"]}) · '
                            f'{renal["stage"]}. Renally cleared drugs need dose review.', 'Laboratory + vitals'))
    elif renal.get('missing'):
        missing.append('creatinine' if 'creatinine' in renal['missing'] else 'weight')
    k = renal.get('potassium')
    if k is not None and k > 5.0:
        factors.append(_factor('Hyperkalaemia', 2 if k > 5.5 else 1, f'K⁺ {k:.1f} mEq/L', 'Laboratory'))
        obs.append(_obs('high' if k > 5.5 else 'moderate', 'Potassium above range', f'K⁺ {k:.1f} mEq/L; review potassium-raising drugs.', 'Laboratory'))

    # ---- laboratory ------------------------------------------------------
    since = now - timedelta(days=30)
    rows = (db.session.query(LabResult, LabTestCatalog)
            .join(LabOrder, LabResult.order_id == LabOrder.id)
            .join(LabTestCatalog, LabOrder.test_id == LabTestCatalog.id)
            .filter(LabOrder.patient_id == patient.id, LabResult.result_date >= since).all())
    abnormal = {t.test_name for r, t in rows if r.is_abnormal and not r.is_critical}
    critical = {t.test_name for r, t in rows if r.is_critical}
    if abnormal:
        factors.append(_factor('Abnormal laboratory results (30 days)', min(len(abnormal), 3),
                               ', '.join(sorted(abnormal)[:5]), 'Laboratory'))
    if critical:
        factors.append(_factor('Critical laboratory value', 2, ', '.join(sorted(critical)), 'Laboratory'))
        obs.append(_obs('critical', 'Critical laboratory value in the last 30 days', ', '.join(sorted(critical)), 'Laboratory'))
    if not rows:
        last = (LabResult.query.join(LabOrder, LabResult.order_id == LabOrder.id)
                .filter(LabOrder.patient_id == patient.id).order_by(LabResult.result_date.desc()).first())
        missing.append('recent laboratory results' if last is None else
                       f'laboratory results newer than {last.result_date.strftime("%d %b %Y")}')

    # ---- vitals / NEWS2 --------------------------------------------------
    vital = VitalSign.query.filter_by(patient_id=patient.id).order_by(VitalSign.recorded_at.desc()).first()
    news = None
    if vital and vital.recorded_at and vital.recorded_at >= now - timedelta(days=7):
        news = news2(vital)
        s = news.get('score') or 0
        if s >= 7:
            factors.append(_factor('NEWS2 high (≥ 7)', 4, f'NEWS2 {s} — {news.get("band")}', 'Vital signs'))
            obs.append(_obs('critical', 'Early-warning score high', f'NEWS2 {s}: {news.get("advice")}', 'Vital signs'))
        elif s >= 5:
            factors.append(_factor('NEWS2 medium (5–6)', 3, f'NEWS2 {s} — {news.get("band")}', 'Vital signs'))
            obs.append(_obs('high', 'Early-warning score medium', f'NEWS2 {s}: {news.get("advice")}', 'Vital signs'))
        elif s >= 1:
            factors.append(_factor('NEWS2 low (1–4)', 1, f'NEWS2 {s}', 'Vital signs'))
    elif vital is None:
        missing.append('vital signs')
    else:
        missing.append(f'vital signs newer than {vital.recorded_at.strftime("%d %b %Y")}')

    # ---- medications -----------------------------------------------------
    items = (PrescriptionItem.query.join(Prescription, PrescriptionItem.prescription_id == Prescription.id)
             .filter(Prescription.patient_id == patient.id, Prescription.status.in_(('Active', 'Dispensed')),
                     PrescriptionItem.status != 'Cancelled').all())
    meds = {}
    for it in items:
        if it.medication:
            meds.setdefault((it.medication.generic_name or '').lower(), it)
    n_meds = len(meds)
    if n_meds >= 10:
        factors.append(_factor('Polypharmacy (10+ medications)', 2, f'{n_meds} active medications', 'Prescriptions'))
    elif n_meds >= 5:
        factors.append(_factor('Polypharmacy (5+ medications)', 1, f'{n_meds} active medications', 'Prescriptions'))
    hr = [cls for cls, names in HIGH_RISK_CLASSES.items() if names & set(meds)]
    if hr:
        factors.append(_factor('High-risk medication class', 1, ', '.join(hr), 'Prescriptions'))
    review = mr.review_patient(patient) if items else None
    if review and review['findings']:
        top = review['findings'][0]
        pts = {'Contraindicated': 3, 'Major': 2, 'Moderate': 1}.get(top['severity'], 0)
        if pts:
            factors.append(_factor('Medication safety finding', pts, f"{top['severity']}: {top['title']}", 'Clinical pharmacist rules'))
        for f in review['findings'][:3]:
            if f['severity'] == 'Info':
                continue
            sev = {'Contraindicated': 'critical', 'Major': 'high', 'Moderate': 'moderate'}.get(f['severity'], 'low')
            obs.append(_obs(sev, f['title'], (f['management'] or f['detail'])[:220], 'Clinical pharmacist rules'))

    # ---- allergies (real ones only) ---------------------------------------
    allergies = documented_allergies(patient)
    if allergies:
        factors.append(_factor('Documented drug allergy', 1, ', '.join(a.substance for a in allergies[:4]), 'Allergy list'))
        obs.append(_obs('moderate', 'Documented allergies',
                        ' · '.join(f'{a.substance}' + (f' ({a.reaction})' if getattr(a, "reaction", None) else '') for a in allergies[:4]),
                        'Allergy list'))

    # ---- admissions ------------------------------------------------------
    current = Admission.query.filter_by(patient_id=patient.id, status='Admitted').first()
    recent = Admission.query.filter(Admission.patient_id == patient.id,
                                    Admission.admitted_at >= now - timedelta(days=365)).count()
    if current:
        factors.append(_factor('Currently admitted', 1,
                               f'{current.ward.name if current.ward else "Ward"} · since {current.admitted_at.strftime("%d %b")}', 'Admissions'))
    if recent >= 2:
        factors.append(_factor('Two or more admissions in 12 months', 2, f'{recent} admissions', 'Admissions'))

    # ---- open alerts -----------------------------------------------------
    alerts = ClinicalAlert.query.filter(ClinicalAlert.patient_id == patient.id,
                                        ClinicalAlert.status.in_(('OPEN', 'ACKNOWLEDGED', 'IN_PROGRESS'))).all()
    # Alerts that merely restate a medication finding already counted above are not double-counted.
    seen_titles = {o['title'] for o in obs}
    crit = [a for a in alerts if a.severity == 'CRITICAL' and a.title not in seen_titles]
    high = [a for a in alerts if a.severity == 'HIGH' and a.title not in seen_titles]
    if crit:
        factors.append(_factor('Open critical alert', 2, crit[0].title, 'Alert engine'))
    elif high:
        factors.append(_factor('Open high-severity alert', 1, high[0].title, 'Alert engine'))
    for a in (crit + high)[:3]:
        obs.append(_obs('critical' if a.severity == 'CRITICAL' else 'high', a.title, (a.message or '')[:220],
                        'Alert engine' + (' · AI-assisted' if a.ai_assisted else ''), f'/clinical/alerts/{a.id}'))

    # ---- follow-up -------------------------------------------------------
    overdue = FollowUp.query.filter(FollowUp.patient_id == patient.id, FollowUp.status == 'Scheduled',
                                    FollowUp.scheduled_for < now).count()
    if overdue:
        obs.append(_obs('moderate', 'Overdue follow-up', f'{overdue} scheduled follow-up(s) past due.', 'Follow-ups'))

    # ---- score -----------------------------------------------------------
    score = sum(f['points'] for f in factors)
    level = 'High' if score >= 10 else 'Moderate' if score >= 5 else 'Low'
    obs.sort(key=lambda o: -SEV_RANK.get(o['severity'], 0))
    if missing:
        obs.append(_obs('info', 'Data not available for scoring', ', '.join(missing) + '.', 'Data quality'))

    last_visit = MedicalRecord.query.filter_by(patient_id=patient.id).order_by(MedicalRecord.visit_date.desc()).first()
    return {
        'score': score, 'level': level, 'scale': {'low': 4, 'moderate': 9},
        'factors': sorted(factors, key=lambda f: -f['points']),
        'observations': obs,
        'missing': missing,
        'renal': renal, 'news2': news, 'vital': vital, 'age': age,
        'allergies': allergies, 'problems': problems, 'diagnoses': diagnoses,
        'medications': list(meds.values()),
        'admission': current,
        'last_visit': last_visit.visit_date if last_visit else None,
        'disclaimer': 'Deterministic risk profile for triage and review; every factor is traceable to the record. Not a diagnosis.',
    }
