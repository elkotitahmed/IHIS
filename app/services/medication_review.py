"""Deterministic clinical-pharmacist review of prescriptions.

Every finding here comes from rules and reference data, never from a language
model, so the output is reproducible and can be shown as *authoritative* next
to any AI summary:

* **Renal function** — latest serum creatinine / eGFR / potassium from the
  laboratory module, weight from nursing vitals, age and sex from the chart;
  creatinine clearance by Cockcroft-Gault (the estimate drug labels use).
* **Renal dose rules** — a curated table of common drugs whose dose or use
  changes with creatinine clearance (thresholds from the FDA / SmPC labels).
* **Drug-drug interactions** — the new medication against *every* other active
  medication of the patient (not only the lines on the same prescription):
  local formulary pairs first (they carry mechanism and management), then the
  DDInter reference for anything the formulary does not cover.
* **Inhibitor + impaired clearance combinations** — e.g. colchicine with a
  P-gp / CYP3A4 inhibitor in renal impairment, where the label contraindicates
  the combination outright.
* **Drug-lab conflicts** — e.g. potassium-raising drugs with an already high
  potassium.
* **Allergy conflicts and therapeutic duplication.**

The result feeds three places: the alert engine at prescribing time
(``flag_prescription_safety``), the pharmacist's prescription page (review
card + pre-filled intervention form) and the AI medication review prompt
(so the model never contradicts the rules).
"""
from datetime import date

from app import db
from app.models import (Allergy, DrugInteraction, LabOrder, LabResult, LabTestCatalog,
                        Prescription, PrescriptionItem, VitalSign)

SEVERITY_RANK = {'Contraindicated': 4, 'Major': 3, 'Moderate': 2, 'Minor': 1, 'Info': 0}
# Finding severity -> ClinicalAlert severity
ALERT_SEVERITY = {'Contraindicated': 'CRITICAL', 'Major': 'HIGH', 'Moderate': 'MODERATE',
                  'Minor': 'LOW', 'Info': 'INFO'}

# Drugs that inhibit P-glycoprotein and/or CYP3A4 strongly enough to matter for
# narrow-therapeutic-index substrates (colchicine, some DOACs, statins).
PGP_CYP3A4_INHIBITORS = {
    'diltiazem': 'moderate CYP3A4 + P-gp inhibitor', 'verapamil': 'moderate CYP3A4 + P-gp inhibitor',
    'clarithromycin': 'strong CYP3A4 + P-gp inhibitor', 'erythromycin': 'moderate CYP3A4 + P-gp inhibitor',
    'ketoconazole': 'strong CYP3A4 + P-gp inhibitor', 'itraconazole': 'strong CYP3A4 + P-gp inhibitor',
    'voriconazole': 'strong CYP3A4 inhibitor', 'posaconazole': 'strong CYP3A4 + P-gp inhibitor',
    'ritonavir': 'strong CYP3A4 + P-gp inhibitor', 'cobicistat': 'strong CYP3A4 + P-gp inhibitor',
    'cyclosporine': 'P-gp inhibitor', 'ciclosporin': 'P-gp inhibitor', 'amiodarone': 'P-gp inhibitor',
    'dronedarone': 'P-gp + CYP3A4 inhibitor', 'fluconazole': 'moderate CYP3A4 inhibitor',
    'grapefruit': 'CYP3A4 inhibitor',
}

ACE_ARB_MRA = {'lisinopril', 'enalapril', 'ramipril', 'captopril', 'perindopril', 'benazepril',
               'quinapril', 'losartan', 'valsartan', 'candesartan', 'irbesartan', 'telmisartan',
               'olmesartan', 'spironolactone', 'eplerenone', 'sacubitril/valsartan'}
POTASSIUM_RAISERS = ACE_ARB_MRA | {'potassium chloride', 'potassium citrate', 'trimethoprim',
                                   'sulfamethoxazole/trimethoprim', 'co-trimoxazole', 'amiloride',
                                   'triamterene'}
NSAIDS = {'ibuprofen', 'naproxen', 'diclofenac', 'indomethacin', 'ketorolac', 'celecoxib', 'meloxicam',
          'piroxicam', 'etoricoxib', 'ketoprofen', 'mefenamic acid'}

# Renal dose rules: generic name -> list of rules.  Each rule fires when the
# estimated CrCl (or eGFR when CrCl cannot be computed) is below ``crcl_lt``.
# Text is deliberately short; it is decision support, the pharmacist decides.
RENAL_RULES = {
    'colchicine': [
        {'crcl_lt': 30, 'severity': 'Major',
         'advice': 'Severe renal impairment: gout flare 0.6 mg as a single dose, course not repeated within '
                   '14 days; prophylaxis 0.3 mg daily. Monitor for neuromyopathy and cytopenias.',
         'source': 'FDA Colcrys label, Dosage in renal impairment'},
        {'crcl_lt': 50, 'severity': 'Moderate',
         'advice': 'Moderate renal impairment: monitor closely for toxicity; consider dose reduction.',
         'source': 'FDA Colcrys label'},
    ],
    'apixaban': [
        {'crcl_lt': 15, 'severity': 'Moderate',
         'advice': 'CrCl <15 mL/min or dialysis: limited data; specialist decision.',
         'source': 'FDA Eliquis label'},
    ],
    'rivaroxaban': [
        {'crcl_lt': 15, 'severity': 'Major', 'advice': 'CrCl <15 mL/min: avoid.', 'source': 'FDA Xarelto label'},
        {'crcl_lt': 50, 'severity': 'Moderate', 'advice': 'CrCl 15-50 mL/min (AF): 15 mg once daily with food.',
         'source': 'FDA Xarelto label'},
    ],
    'dabigatran': [
        {'crcl_lt': 30, 'severity': 'Major', 'advice': 'CrCl <30 mL/min: avoid (EU) / 75 mg twice daily (US label).',
         'source': 'Pradaxa label'},
    ],
    'enoxaparin': [
        {'crcl_lt': 30, 'severity': 'Major',
         'advice': 'CrCl <30 mL/min: treatment 1 mg/kg once daily; prophylaxis 30 mg once daily.',
         'source': 'FDA Lovenox label'},
    ],
    'metformin': [
        {'crcl_lt': 30, 'severity': 'Contraindicated', 'advice': 'eGFR <30: contraindicated (lactic acidosis).',
         'source': 'FDA Glucophage label'},
        {'crcl_lt': 45, 'severity': 'Major',
         'advice': 'eGFR 30-45: do not initiate; if already taking, reduce dose (max 1000 mg/day) and monitor.',
         'source': 'FDA Glucophage label'},
    ],
    'glibenclamide': [{'crcl_lt': 50, 'severity': 'Major', 'advice': 'Avoid: prolonged hypoglycaemia.',
                       'source': 'SmPC'}],
    'glyburide': [{'crcl_lt': 50, 'severity': 'Major', 'advice': 'Avoid: prolonged hypoglycaemia.',
                   'source': 'FDA label'}],
    'sitagliptin': [
        {'crcl_lt': 30, 'severity': 'Moderate', 'advice': '25 mg once daily.', 'source': 'FDA Januvia label'},
        {'crcl_lt': 45, 'severity': 'Moderate', 'advice': '50 mg once daily.', 'source': 'FDA Januvia label'},
    ],
    'lisinopril': [
        {'crcl_lt': 30, 'severity': 'Moderate',
         'advice': 'CrCl <30 mL/min: start 2.5-5 mg daily; monitor potassium and creatinine.',
         'source': 'FDA Zestril label'},
    ],
    'enalapril': [{'crcl_lt': 30, 'severity': 'Moderate', 'advice': 'Start 2.5 mg daily; monitor K+ and creatinine.',
                   'source': 'FDA label'}],
    'ramipril': [{'crcl_lt': 40, 'severity': 'Moderate', 'advice': 'Start 1.25 mg daily, max 5 mg daily.',
                  'source': 'FDA label'}],
    'spironolactone': [{'crcl_lt': 30, 'severity': 'Major', 'advice': 'Avoid: hyperkalaemia risk.',
                        'source': 'FDA Aldactone label'}],
    'allopurinol': [
        {'crcl_lt': 30, 'severity': 'Moderate', 'advice': 'Start 50 mg daily and titrate slowly to urate target.',
         'source': 'ACR gout guideline 2020'},
    ],
    'gabapentin': [{'crcl_lt': 60, 'severity': 'Moderate', 'advice': 'Dose by CrCl (e.g. 200-700 mg/day for 30-59).',
                    'source': 'FDA Neurontin label'}],
    'pregabalin': [{'crcl_lt': 60, 'severity': 'Moderate', 'advice': 'Dose by CrCl (max 300 mg/day for 30-60).',
                    'source': 'FDA Lyrica label'}],
    'digoxin': [{'crcl_lt': 60, 'severity': 'Moderate', 'advice': 'Reduce dose; monitor level and potassium.',
                 'source': 'FDA Lanoxin label'}],
    'amoxicillin': [{'crcl_lt': 30, 'severity': 'Moderate', 'advice': 'Max 500 mg per dose; extend interval to 12 h.',
                     'source': 'FDA label'}],
    'ciprofloxacin': [{'crcl_lt': 50, 'severity': 'Moderate', 'advice': 'Reduce dose (250-500 mg every 12-18 h).',
                       'source': 'FDA Cipro label'}],
    'levofloxacin': [{'crcl_lt': 50, 'severity': 'Moderate', 'advice': 'Reduce dose per label table.',
                      'source': 'FDA Levaquin label'}],
    'nitrofurantoin': [{'crcl_lt': 30, 'severity': 'Major', 'advice': 'Contraindicated below 30 mL/min.',
                        'source': 'FDA label'}],
    'sulfamethoxazole/trimethoprim': [{'crcl_lt': 30, 'severity': 'Moderate', 'advice': 'Half the usual dose (15-30 mL/min); avoid <15.',
                                       'source': 'FDA Bactrim label'}],
    'acyclovir': [{'crcl_lt': 50, 'severity': 'Moderate', 'advice': 'Extend dosing interval.', 'source': 'FDA label'}],
    'valacyclovir': [{'crcl_lt': 50, 'severity': 'Moderate', 'advice': 'Reduce dose per label table.', 'source': 'FDA label'}],
    'famotidine': [{'crcl_lt': 50, 'severity': 'Minor', 'advice': 'Half dose or extend interval.', 'source': 'FDA label'}],
    'morphine': [{'crcl_lt': 30, 'severity': 'Moderate', 'advice': 'Active metabolite accumulates: reduce dose / prefer alternative.',
                  'source': 'SmPC'}],
    'codeine': [{'crcl_lt': 30, 'severity': 'Moderate', 'advice': 'Reduce dose; metabolite accumulation.', 'source': 'SmPC'}],
    'metoclopramide': [{'crcl_lt': 60, 'severity': 'Moderate', 'advice': 'Halve the dose.', 'source': 'FDA label'}],
    'lithium': [{'crcl_lt': 30, 'severity': 'Major', 'advice': 'Avoid; if essential, reduced dose with close level monitoring.',
                 'source': 'SmPC'}],
    'baclofen': [{'crcl_lt': 30, 'severity': 'Major', 'advice': 'Avoid or use very low dose; toxicity reported.',
                  'source': 'SmPC'}],
    'potassium chloride': [{'crcl_lt': 30, 'severity': 'Major', 'advice': 'High hyperkalaemia risk; monitor potassium.',
                            'source': 'SmPC'}],
}
for _n in NSAIDS:
    RENAL_RULES.setdefault(_n, [
        {'crcl_lt': 30, 'severity': 'Major', 'advice': 'Avoid NSAIDs: acute kidney injury and hyperkalaemia risk.',
         'source': 'KDIGO CKD guideline'},
        {'crcl_lt': 60, 'severity': 'Moderate', 'advice': 'Avoid if possible; shortest course, recheck creatinine.',
         'source': 'KDIGO CKD guideline'},
    ])


# ---------------------------------------------------------------------------
# Chart data
# ---------------------------------------------------------------------------
def _num(value):
    try:
        return float(str(value).replace(',', '.').split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def latest_lab(patient_id, keywords, exclude=()):
    """Latest numeric result whose test name contains one of ``keywords``.
    Returns ``(value, unit, date)`` or ``(None, None, None)``."""
    q = (db.session.query(LabResult, LabTestCatalog)
         .join(LabOrder, LabResult.order_id == LabOrder.id)
         .join(LabTestCatalog, LabOrder.test_id == LabTestCatalog.id)
         .filter(LabOrder.patient_id == patient_id)
         .order_by(LabResult.result_date.desc(), LabResult.id.desc()))
    for res, test in q.all():
        name = (test.test_name or '').lower()
        if any(k in name for k in keywords) and not any(x in name for x in exclude):
            v = _num(res.result_value)
            if v is not None:
                return v, res.result_unit or test.unit, res.result_date
    return None, None, None


def _age(patient, today=None):
    dob = patient.date_of_birth
    if not dob:
        return None
    today = today or date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _weight(patient):
    v = (VitalSign.query.filter(VitalSign.patient_id == patient.id, VitalSign.weight_kg.isnot(None))
         .order_by(VitalSign.recorded_at.desc()).first())
    return v.weight_kg if v else None


def cockcroft_gault(age, weight_kg, scr_mg_dl, female=False):
    if not all([age, weight_kg, scr_mg_dl]) or scr_mg_dl <= 0:
        return None
    crcl = ((140 - age) * weight_kg) / (72.0 * scr_mg_dl)
    return round(crcl * (0.85 if female else 1.0), 1)


def ckd_stage(value):
    if value is None:
        return None
    if value >= 90:
        return 'G1 (normal)'
    if value >= 60:
        return 'G2 (mild)'
    if value >= 45:
        return 'G3a (mild-moderate)'
    if value >= 30:
        return 'G3b (moderate-severe)'
    if value >= 15:
        return 'G4 (severe)'
    return 'G5 (kidney failure)'


def renal_function(patient):
    """Everything the dose rules need, with provenance so the page can show it."""
    scr, scr_unit, scr_date = latest_lab(patient.id, ('creatinine',), exclude=('clearance', 'urine'))
    egfr, _, egfr_date = latest_lab(patient.id, ('egfr', 'gfr'))
    k, _, k_date = latest_lab(patient.id, ('potassium',))
    age = _age(patient)
    weight = _weight(patient)
    female = (patient.gender or '').lower().startswith('f')
    scr_mg = None
    if scr is not None:
        # µmol/L (SI) -> mg/dL; anything else is taken as mg/dL
        scr_mg = round(scr / 88.4, 2) if scr_unit and 'mol' in scr_unit.lower() else scr
    crcl = cockcroft_gault(age, weight, scr_mg, female)
    effective = crcl if crcl is not None else egfr
    return {
        'age': age, 'sex': patient.gender, 'weight_kg': weight,
        'scr': scr, 'scr_unit': scr_unit, 'scr_date': scr_date,
        'egfr': egfr, 'egfr_date': egfr_date,
        'potassium': k, 'potassium_date': k_date,
        'crcl': crcl, 'effective': effective, 'stage': ckd_stage(effective),
        'method': 'Cockcroft-Gault' if crcl is not None else ('reported eGFR' if egfr is not None else None),
        'missing': [n for n, v in (('age', age), ('weight', weight), ('creatinine', scr)) if v is None],
    }


def active_items(patient_id, exclude_rx_id=None):
    q = (PrescriptionItem.query
         .join(Prescription, PrescriptionItem.prescription_id == Prescription.id)
         .filter(Prescription.patient_id == patient_id,
                 Prescription.status.in_(('Active', 'Dispensed')),
                 PrescriptionItem.status != 'Cancelled'))
    if exclude_rx_id:
        q = q.filter(Prescription.id != exclude_rx_id)
    return [it for it in q.all() if it.medication]


def _gname(med):
    return (med.generic_name or med.brand_name or '').strip().lower()


# ---------------------------------------------------------------------------
# Finding builders
# ---------------------------------------------------------------------------
def _finding(ftype, severity, medication, title, detail='', management='', source='', with_=None,
             category='OTHER'):
    return {'type': ftype, 'severity': severity, 'medication': medication, 'with': with_,
            'title': title, 'detail': detail, 'management': management, 'source': source,
            'category': category}


def interaction_findings(med, others):
    """``others``: iterable of Medication rows already on the patient."""
    out, seen = [], set()
    for other in others:
        if other.id == med.id or _gname(other) in seen:
            continue
        row = (DrugInteraction.query.filter(
            db.or_(db.and_(DrugInteraction.medication_a_id == med.id, DrugInteraction.medication_b_id == other.id),
                   db.and_(DrugInteraction.medication_a_id == other.id, DrugInteraction.medication_b_id == med.id)))
            .first())
        if row:
            seen.add(_gname(other))
            out.append(_finding('INTERACTION', row.severity or 'Moderate', med.generic_name,
                                ' + '.join(sorted((med.generic_name, other.generic_name))), row.description or '',
                                row.management or '', 'Local formulary' + (f' · {row.mechanism}' if row.mechanism else ''),
                                with_=other.generic_name, category='INTERACTION'))
    try:
        from app.services import drug_interactions as ddi
        names = [med.generic_name] + [o.generic_name for o in others if o.id != med.id and o.generic_name]
        for row in ddi.check(names):
            other = row['b'] if row['a'].lower() == _gname(med) else row['a'] if row['b'].lower() == _gname(med) else None
            if other and other.lower() not in seen and other.lower() != _gname(med):
                seen.add(other.lower())
                out.append(_finding('INTERACTION', row['level'] if row['level'] in SEVERITY_RANK else 'Moderate',
                                    med.generic_name, ' + '.join(sorted((med.generic_name, other))),
                                    'Documented interaction in the DDInter reference.', '', 'DDInter',
                                    with_=other, category='INTERACTION'))
    except Exception:  # noqa: BLE001 - reference data is optional
        pass
    return out


def renal_findings(med, renal, others):
    out = []
    name = _gname(med)
    eff = renal.get('effective')
    rules = RENAL_RULES.get(name, [])
    if eff is not None and rules:
        rule = next((r for r in sorted(rules, key=lambda r: r['crcl_lt']) if eff < r['crcl_lt']), None)
        if rule:
            out.append(_finding('RENAL', rule['severity'], med.generic_name,
                                f'{med.generic_name}: dose adjustment for CrCl {eff:.0f} mL/min',
                                rule['advice'], '', rule['source'], category='RENAL_ADJUSTMENT'))
    # Colchicine + P-gp / CYP3A4 inhibitor in renal impairment: label contraindication.
    # Evaluated from either side of the pair so both prescriptions raise the same finding.
    colch = None
    if name == 'colchicine':
        inhib = [o for o in others if _gname(o) in PGP_CYP3A4_INHIBITORS]
    elif name in PGP_CYP3A4_INHIBITORS:
        colch = next((o for o in others if _gname(o) == 'colchicine'), None)
        inhib = [med] if colch else []
    else:
        inhib = []
    if inhib and colch is not None:
        # reviewing the inhibitor line: report it as the colchicine pair
        med, others = colch, [med]
    if name in ('colchicine',) or colch is not None:
        if inhib and eff is not None and eff < 50:
            names = ', '.join(o.generic_name for o in inhib)
            out.append(_finding(
                'INTERACTION', 'Contraindicated', med.generic_name,
                f'Colchicine + {names} with reduced renal clearance (CrCl {eff:.0f} mL/min)',
                f'{names}: {PGP_CYP3A4_INHIBITORS[_gname(inhib[0])]}. Colchicine clearance falls with both the '
                'inhibitor and the renal impairment; fatal toxicity (rhabdomyolysis, pancytopenia, multi-organ '
                'failure) has been reported. The label states patients with renal or hepatic impairment should '
                'not receive colchicine with P-gp or strong CYP3A4 inhibitors.',
                'Prefer an alternative: oral prednisone 30-40 mg daily for 5 days (or intra-articular '
                'corticosteroid). If colchicine is unavoidable: a single reduced dose (0.3-0.6 mg), no repeat '
                'within 14 days, monitor CBC and CK.',
                'FDA Colcrys label, Contraindications / Drug interactions', with_=names,
                category='CONTRAINDICATION'))
        elif inhib:
            names = ', '.join(o.generic_name for o in inhib)
            out.append(_finding('INTERACTION', 'Major', med.generic_name, f'Colchicine + {names}',
                                f'{names} raises colchicine exposure ({PGP_CYP3A4_INHIBITORS[_gname(inhib[0])]}).',
                                'Reduce the flare dose (e.g. 1.2 mg once with a moderate CYP3A4 inhibitor, '
                                '0.6 mg once with a P-gp inhibitor); do not repeat within 3 days.',
                                'FDA Colcrys label', with_=names, category='INTERACTION'))
    # Apixaban dose-reduction criteria (age >= 80, weight <= 60 kg, SCr >= 1.5 mg/dL)
    if name == 'apixaban' and renal.get('scr') is not None:
        crit = []
        if renal.get('age') is not None and renal['age'] >= 80:
            crit.append('age ≥ 80')
        if renal.get('weight_kg') is not None and renal['weight_kg'] <= 60:
            crit.append('weight ≤ 60 kg')
        if renal['scr'] >= 1.5:
            crit.append(f'SCr {renal["scr"]:.1f} ≥ 1.5 mg/dL')
        if len(crit) >= 2:
            out.append(_finding('RENAL', 'Major', med.generic_name,
                                'Apixaban: dose-reduction criteria met (' + ', '.join(crit) + ')',
                                'Two or more of age ≥ 80, weight ≤ 60 kg, SCr ≥ 1.5 mg/dL.',
                                'Reduce to 2.5 mg twice daily for non-valvular AF.', 'FDA Eliquis label',
                                category='DOSE'))
        elif len(crit) == 1:
            out.append(_finding('RENAL', 'Info', med.generic_name,
                                f'Apixaban: 1 of 3 dose-reduction criteria ({crit[0]})',
                                'Dose reduction needs ≥ 2 criteria; 5 mg twice daily remains appropriate.',
                                'Re-check if age, weight or creatinine change.', 'FDA Eliquis label',
                                category='DOSE'))
    return out


def lab_findings(med, renal):
    out = []
    name = _gname(med)
    k = renal.get('potassium')
    if k is not None and name in POTASSIUM_RAISERS:
        if k > 5.5:
            out.append(_finding('LAB', 'Major', med.generic_name, f'{med.generic_name} with potassium {k:.1f} mEq/L',
                                'Hyperkalaemia: this drug reduces potassium excretion.',
                                'Hold and recheck potassium; ECG if ≥ 6.0.', 'KDIGO', category='DOSE'))
        elif k > 5.0:
            out.append(_finding('LAB', 'Moderate', med.generic_name,
                                f'{med.generic_name} with potassium {k:.1f} mEq/L',
                                'Mild hyperkalaemia; ACE inhibitors / ARBs / MRAs reduce renal potassium excretion.',
                                'Monitor potassium closely; review dose; consider a potassium binder if it stays '
                                '> 5.0 mEq/L.', 'KDIGO', category='DOSE'))
    return out


def allergy_findings(med, patient):
    from app.services.ai.copilot import allergy_conflict
    out = []
    for a in Allergy.query.filter_by(patient_id=patient.id).all():
        if (a.status or 'Active') == 'Inactive':
            continue
        reason = allergy_conflict(a.substance, med.generic_name, med.brand_name)
        if reason:
            out.append(_finding('ALLERGY', 'Contraindicated', med.generic_name,
                                f'{med.generic_name}: {reason}', a.reaction or '', '', 'Allergy list',
                                category='ALLERGY'))
    free = (patient.allergies or '').lower()
    if free and 'nkda' not in free and _gname(med) and _gname(med) in free:
        out.append(_finding('ALLERGY', 'Contraindicated', med.generic_name,
                            f'{med.generic_name} appears in the free-text allergy field', patient.allergies, '',
                            'Chart', category='ALLERGY'))
    return out


def duplicate_findings(med, others):
    out = []
    for o in others:
        if o.id != med.id and _gname(o) == _gname(med):
            out.append(_finding('DUPLICATE', 'Moderate', med.generic_name,
                                f'{med.generic_name} is already on another active prescription', '',
                                'Confirm intended; cancel the duplicate line.', 'Chart',
                                with_=o.generic_name, category='DUPLICATE_THERAPY'))
            break
    return out


def _level(findings):
    top = max((SEVERITY_RANK.get(f['severity'], 0) for f in findings), default=-1)
    return {4: 'CRITICAL', 3: 'HIGH', 2: 'MODERATE', 1: 'LOW', 0: 'OK'}.get(top, 'OK')


def _sort(findings):
    return sorted(findings, key=lambda f: -SEVERITY_RANK.get(f['severity'], 0))


def _pair_key(f):
    return tuple(sorted(((f.get('medication') or '').lower(), (f.get('with') or '').lower())))


def _dedupe(findings):
    """One finding per (drug pair, type): the same pair seen from both lines of a
    prescription, or a label contraindication that supersedes the plain
    formulary pair, must not produce two alerts."""
    findings = _sort(findings)
    out, seen = [], set()
    contra_pairs = {_pair_key(f) for f in findings if f['severity'] == 'Contraindicated' and f.get('with')}
    for f in findings:
        if f.get('with'):
            key = (f['type'], _pair_key(f))
            if key in seen:
                continue
            if f['type'] == 'INTERACTION' and f['severity'] != 'Contraindicated' and _pair_key(f) in contra_pairs:
                continue
            seen.add(key)
        out.append(f)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def review_prescription(rx):
    """Review every line of ``rx`` against the patient's chart and other active
    medications. Returns a dict safe to pass to templates."""
    patient = rx.patient
    renal = renal_function(patient)
    background = active_items(patient.id, exclude_rx_id=rx.id)
    background_meds = [it.medication for it in background]
    lines = [it for it in rx.items if it.medication and it.status != 'Cancelled']
    items, flat = [], []
    for it in lines:
        med = it.medication
        others = background_meds + [o.medication for o in lines if o.id != it.id]
        findings = (allergy_findings(med, patient) + interaction_findings(med, others)
                    + renal_findings(med, renal, others) + lab_findings(med, renal)
                    + duplicate_findings(med, background_meds))
        findings = _dedupe(findings)
        items.append({'item': it, 'medication': med, 'findings': findings, 'level': _level(findings)})
        flat.extend(findings)
    flat = _dedupe(flat)
    return {'items': items, 'findings': flat, 'level': _level(flat), 'renal': renal,
            'background': background,
            'counts': {s: sum(1 for f in flat if f['severity'] == s) for s in SEVERITY_RANK}}


def review_patient(patient):
    """Review the whole active medication list (each line against the rest)."""
    renal = renal_function(patient)
    items_all = active_items(patient.id)
    meds = [it.medication for it in items_all]
    items, flat, seen_pairs = [], [], set()
    for it in items_all:
        med = it.medication
        findings = (allergy_findings(med, patient) + interaction_findings(med, meds)
                    + renal_findings(med, renal, meds) + lab_findings(med, renal))
        kept = []
        for f in _dedupe(findings):
            key = (f['type'], _pair_key(f))
            if f['with'] and key in seen_pairs:
                continue
            seen_pairs.add(key)
            kept.append(f)
        items.append({'item': it, 'medication': med, 'findings': kept, 'level': _level(kept)})
        flat.extend(kept)
    flat = _dedupe(flat)
    return {'items': items, 'findings': flat, 'level': _level(flat), 'renal': renal,
            'background': items_all,
            'counts': {s: sum(1 for f in flat if f['severity'] == s) for s in SEVERITY_RANK}}


def summary_text(review, limit=12):
    """Plain-text block for AI prompts (rules are authoritative)."""
    r = review['renal']
    lines = []
    if r.get('effective') is not None:
        lines.append(f"Renal function: SCr {r['scr']} {r.get('scr_unit') or ''}, CrCl {r['crcl']} mL/min "
                     f"({r['method']}), stage {r['stage']}; potassium {r['potassium']}.")
    for f in review['findings'][:limit]:
        lines.append(f"- [{f['severity']}] {f['title']}. {f['detail']} {('Management: ' + f['management']) if f['management'] else ''}".strip())
    return '\n'.join(lines) if lines else 'No rule-based findings.'
