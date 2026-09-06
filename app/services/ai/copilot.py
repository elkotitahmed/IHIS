"""Physician AI Clinical Copilot.

One centralised catalogue of explicit actions. Every action:

1. builds a **verified** section from the chart (deterministic, always shown), and
2. optionally adds an **AI-assisted** section through ``platform.run_ai``
   (budgeted, cached, sanitised, audited; never required).

Nothing here writes to the chart. The physician copies, edits and signs.
"""
import json

from app.services.ai import platform
from app.services.ai.clinical_context import build_context, context_fingerprint
from app.services.ai.patient_education import (QUESTIONS_TO_ASK, explain_medication,
                                               explain_test, find_terms)

GROUPS = [
    ('PATIENT', 'Patient', 'المريض', 'fa-user'),
    ('DIAGNOSTICS', 'Diagnostics', 'التشخيصات', 'fa-vials'),
    ('REASONING', 'Clinical Reasoning', 'الاستدلال السريري', 'fa-lightbulb'),
    ('DOCUMENTATION', 'Documentation', 'التوثيق', 'fa-file-lines'),
    ('SAFETY', 'Safety', 'السلامة', 'fa-shield-halved'),
    ('PREDICTIVE', 'Predictive AI', 'الذكاء التنبؤي', 'fa-wand-magic-sparkles'),
    ('COMMUNICATION', 'Patient Communication', 'التواصل مع المريض', 'fa-comment-medical'),
]

# key: (group, label_en, label_ar, heavy, roles)
_ALL = ('Doctor', 'Admin', 'SuperAdmin')
ACTIONS = {
    'patient.summary': ('PATIENT', 'Smart Patient Summary', 'ملخص ذكي للمريض', False, _ALL + ('Nurse', 'Dentist', 'Physiotherapist')),
    'patient.encounter': ('PATIENT', 'Summarize Current Encounter', 'ملخص الزيارة الحالية', False, _ALL + ('Nurse',)),
    'patient.problems': ('PATIENT', 'Review Active Problems', 'مراجعة المشاكل النشطة', False, _ALL + ('Nurse', 'Dentist', 'Physiotherapist', 'Pharmacist')),
    'patient.medications': ('PATIENT', 'Review Medications', 'مراجعة الأدوية', False, _ALL + ('Nurse', 'Pharmacist', 'Dentist')),
    'patient.allergies': ('PATIENT', 'Review Allergies', 'مراجعة الحساسية', False, _ALL + ('Nurse', 'Pharmacist', 'Dentist', 'Physiotherapist')),
    'patient.timeline': ('PATIENT', 'Summarize Timeline', 'ملخص الخط الزمني', False, _ALL + ('Nurse',)),
    'patient.previsit': ('PATIENT', 'Pre-Visit Summary', 'ملخص ما قبل الزيارة', False, _ALL),
    'patient.inpatient_daily': ('PATIENT', 'Inpatient Daily Summary', 'الملخص اليومي للمريض المنوّم', False, _ALL + ('Nurse',)),
    'dx.labs': ('DIAGNOSTICS', 'Summarize Labs', 'ملخص المختبر', False, _ALL + ('Nurse', 'Pharmacist')),
    'dx.trends': ('DIAGNOSTICS', 'Explain Lab Trends', 'شرح اتجاهات النتائج', False, _ALL + ('Nurse',)),
    'dx.radiology': ('DIAGNOSTICS', 'Summarize Radiology', 'ملخص الأشعة', False, _ALL + ('Radiologist', 'RadiologyTechnician')),
    'dx.important': ('DIAGNOSTICS', 'Highlight Important Results', 'أهم النتائج', False, _ALL + ('Nurse',)),
    'reasoning.differential': ('REASONING', 'Differential Considerations', 'اعتبارات التشخيص التفريقي', True, _ALL),
    'reasoning.red_flags': ('REASONING', 'Red Flags', 'علامات الخطر', False, _ALL + ('Nurse',)),
    'reasoning.questions': ('REASONING', 'Suggested Questions', 'أسئلة مقترحة', True, _ALL),
    'reasoning.investigations': ('REASONING', 'Suggested Investigations', 'فحوصات مقترحة', True, _ALL),
    'doc.hpi': ('DOCUMENTATION', 'Draft HPI', 'مسودة تاريخ الشكوى', True, _ALL),
    'doc.structure': ('DOCUMENTATION', 'Structure Note (SOAP)', 'هيكلة الملاحظة', True, _ALL),
    'doc.assessment': ('DOCUMENTATION', 'Draft Assessment', 'مسودة التقييم', True, _ALL),
    'doc.plan': ('DOCUMENTATION', 'Draft Plan', 'مسودة الخطة', True, _ALL),
    'doc.encounter_summary': ('DOCUMENTATION', 'Summarize Encounter', 'ملخص الزيارة', False, _ALL),
    'doc.followup': ('DOCUMENTATION', 'Draft Follow-up', 'مسودة المتابعة', False, _ALL),
    'doc.postvisit': ('DOCUMENTATION', 'Post-Visit Summary & Instructions', 'ملخص ما بعد الزيارة', True, _ALL),
    'doc.discharge': ('DOCUMENTATION', 'AI Discharge Draft', 'مسودة الخروج', True, _ALL),
    'doc.referral': ('DOCUMENTATION', 'Referral Summary', 'ملخص الإحالة', False, _ALL),
    'safety.medication_review': ('SAFETY', 'Medication Review', 'مراجعة الأدوية', False, _ALL + ('Pharmacist', 'Nurse')),
    'safety.allergy_review': ('SAFETY', 'Allergy Review', 'مراجعة الحساسية', False, _ALL + ('Pharmacist', 'Nurse', 'Dentist')),
    'safety.interactions': ('SAFETY', 'Interaction Review', 'مراجعة التداخلات', False, _ALL + ('Pharmacist', 'Nurse', 'Dentist')),
    'safety.reconciliation': ('SAFETY', 'Reconciliation Summary', 'ملخص مطابقة الأدوية', False, _ALL + ('Pharmacist',)),
    'comm.summary': ('COMMUNICATION', 'Patient-friendly Summary', 'ملخص مبسّط للمريض', True, _ALL + ('Nurse', 'Dentist', 'Physiotherapist')),
    'comm.education': ('COMMUNICATION', 'Patient Education', 'تثقيف المريض', True, _ALL + ('Nurse', 'Pharmacist', 'Dentist', 'Physiotherapist')),
    'comm.terms': ('COMMUNICATION', 'Explain Medical Terms', 'شرح المصطلحات الطبية', False, _ALL + ('Nurse', 'Dentist', 'Physiotherapist', 'Pharmacist')),
}

DISCLAIMER = ('AI-assisted output for a licensed clinician. It is not a diagnosis, '
              'not an order and never changes the chart. Review, edit and confirm.')


def catalogue(role_names):
    """Groups + actions visible to the given roles."""
    roles = set(role_names or ())
    out = []
    for key, label, label_ar, icon in GROUPS:
        acts = [{'key': k, 'label': v[1], 'label_ar': v[2], 'heavy': v[3]}
                for k, v in ACTIONS.items() if v[0] == key and roles & set(v[4])]
        if key == 'PREDICTIVE':
            out.append({'key': key, 'label': label, 'label_ar': label_ar, 'icon': icon,
                        'actions': [], 'predictive': predictive_catalogue(roles)})
        elif acts:
            out.append({'key': key, 'label': label, 'label_ar': label_ar, 'icon': icon, 'actions': acts})
    return out


def allowed(action_key, role_names):
    spec = ACTIONS.get(action_key)
    return bool(spec) and bool(set(role_names or ()) & set(spec[4]))


def predictive_catalogue(roles=None):
    """Exactly three predictive capabilities with an honest status."""
    from flask import url_for
    from app.services.ai.skin_lesion_classification import skin_model_available
    from app.services.ai.tooth_segmentation import tooth_model_available
    from app.services.radiology_critical_ai import RadiologyCriticalAI
    roles = set(roles or ())

    def _safe(fn):
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False

    def _rad_available():
        try:
            svc = RadiologyCriticalAI()
            return all((svc.package_dir / n).is_file() for n in
                       ('critical_results_model_negation.py', 'negation_model.pkl',
                        'negation_vectorizer.pkl'))
        except Exception:  # noqa: BLE001
            return False

    derm = _safe(skin_model_available)
    tooth = _safe(tooth_model_available)
    rad = _rad_available()
    items = [
        {'key': 'dermatology', 'label': 'Dermatology AI', 'label_ar': 'ذكاء الجلدية',
         'desc': 'AI-assisted skin lesion analysis', 'desc_ar': 'تحليل آفات الجلد بمساعدة الذكاء الاصطناعي',
         'icon': 'fa-person-circle-question', 'status': 'AVAILABLE' if derm else 'COMING SOON',
         'url': url_for('ai.skin_lesion_detection'),
         'roles': ('Doctor', 'Dentist', 'Nurse', 'Admin', 'SuperAdmin')},
        {'key': 'radiology', 'label': 'Radiology AI', 'label_ar': 'ذكاء الأشعة',
         'desc': 'AI-assisted imaging & report analysis', 'desc_ar': 'تحليل التقارير والصور بمساعدة الذكاء الاصطناعي',
         'icon': 'fa-x-ray', 'status': 'AVAILABLE' if rad else 'COMING SOON',
         'url': url_for('copilot.radiology_ai'),
         'roles': ('Doctor', 'Radiologist', 'RadiologyTechnician', 'Admin', 'SuperAdmin')},
        {'key': 'dentistry', 'label': 'Dentistry AI', 'label_ar': 'ذكاء الأسنان',
         'desc': 'AI-assisted dental finding analysis', 'desc_ar': 'تحليل نتائج الأسنان بمساعدة الذكاء الاصطناعي',
         'icon': 'fa-tooth', 'status': 'AVAILABLE',   # chart analysis is local-first; segmentation adds when the model exists
         'model_status': 'AVAILABLE' if tooth else 'LIMITED',
         'url': url_for('copilot.dentistry_ai'),
         'roles': ('Dentist', 'Doctor', 'Admin', 'SuperAdmin')},
    ]
    if roles:
        for it in items:
            it['allowed'] = bool(roles & set(it['roles']))
    return items


# ---------------------------------------------------------------------------
# Verified (local) sections
# ---------------------------------------------------------------------------
def _sec(title, items, kind='list'):
    return {'title': title, 'items': [i for i in items if i], 'kind': kind}


def _fmt_lab(l):
    flag = ' — CRITICAL' if l['critical'] else (' — abnormal' if l['abnormal'] else '')
    ref = f" (ref {l['reference']})" if l.get('reference') else ''
    return f"{l['date'] or ''} {l['test']}: {l['value']} {l['unit'] or ''}{ref}{flag}".strip()


def _fmt_med(m):
    return f"{m['name']}{' (' + m['brand'] + ')' if m.get('brand') else ''} {m['dosage'] or ''} {m['frequency'] or ''}".strip()


def _fmt_allergy(a):
    return f"{a['substance']}{' — ' + a['reaction'] if a.get('reaction') else ''}{' (' + a['severity'] + ')' if a.get('severity') else ''}"


def _interaction_findings(ctx):
    """Deterministic drug–drug interactions and duplicates among active meds."""
    from app.models import DrugInteraction, Medication
    ids = [m['id'] for m in ctx['medications']]
    out = []
    if len(ids) >= 2:
        rows = (DrugInteraction.query
                .filter(DrugInteraction.medication_a_id.in_(ids),
                        DrugInteraction.medication_b_id.in_(ids)).all())
        names = {m['id']: m['name'] for m in ctx['medications']}
        for r in rows:
            out.append(f"{names.get(r.medication_a_id)} + {names.get(r.medication_b_id)}: "
                       f"{r.severity or 'Moderate'} — {r.description or 'documented interaction'}"
                       + (f". Management: {r.management}" if getattr(r, 'management', None) else ''))
    seen = {}
    for m in ctx['medications']:
        key = (m['name'] or '').strip().lower()
        if key in seen:
            out.append(f"Duplicate therapy: {m['name']} appears on more than one active prescription")
        seen[key] = True
    del Medication
    return out


# Deterministic cross-reactivity classes (allergy keyword -> drug names).
ALLERGY_CLASSES = {
    'penicillin': ('penicillin', 'amoxicillin', 'ampicillin', 'piperacillin', 'flucloxacillin',
                   'co-amoxiclav', 'amoxicillin-clavulanate', 'augmentin'),
    'sulfa': ('sulfamethoxazole', 'co-trimoxazole', 'sulfasalazine', 'sulfadiazine'),
    'sulphonamide': ('sulfamethoxazole', 'co-trimoxazole', 'sulfasalazine', 'sulfadiazine'),
    'nsaid': ('ibuprofen', 'naproxen', 'diclofenac', 'ketorolac', 'celecoxib', 'aspirin', 'indomethacin'),
    'aspirin': ('aspirin', 'acetylsalicylic'),
    'cephalosporin': ('cefalexin', 'cephalexin', 'cefuroxime', 'ceftriaxone', 'cefixime', 'cefazolin'),
    'codeine': ('codeine', 'tramadol', 'morphine', 'oxycodone'),
    'iodine': ('iodinated contrast', 'iohexol', 'iopamidol', 'povidone'),
}


def allergy_conflict(allergy_substance, med_name, brand=None):
    """Return a reason string when a medication conflicts with a documented
    allergy (exact/substring match or a known cross-reactivity class)."""
    al = (allergy_substance or '').strip().lower()
    med = (med_name or '').strip().lower()
    brand = (brand or '').strip().lower()
    if not al or not med:
        return None
    if len(al) >= 4 and (al in med or (brand and al in brand) or med in al):
        return f'documented allergy to {allergy_substance}'
    for cls, members in ALLERGY_CLASSES.items():
        if cls in al and any(m in med or (brand and m in brand) for m in members):
            return f'{cls} class cross-reactivity with documented allergy to {allergy_substance}'
    return None


def _allergy_conflicts(ctx):
    conflicts = []
    for m in ctx['medications']:
        for a in ctx['allergies']:
            reason = allergy_conflict(a['substance'], m['name'], m.get('brand'))
            if reason:
                conflicts.append(f"{m['name']}: {reason}")
    return conflicts


def _red_flags(ctx):
    flags = []
    for v in ctx['vitals'][:1]:
        for f in v['flags']:
            flags.append(f"Vitals ({v['date']}): {f}")
    for l in ctx['labs']:
        if l['critical']:
            flags.append(f"Critical lab: {l['test']} {l['value']} {l['unit'] or ''} ({l['date']})")
    for im in ctx['imaging']:
        text = f"{im['impression'] or ''} {im['findings'] or ''}".lower()
        for kw in ('pneumothorax', 'hemorrhage', 'haemorrhage', 'dissection', 'embol', 'free air',
                   'perforation', 'mass', 'fracture', 'ischemi', 'infarct'):
            if kw in text:
                flags.append(f"Imaging ({im['study']}, {im['date']}): mentions '{kw}'")
                break
    for a in ctx['allergies']:
        if (a.get('severity') or '').lower() in ('severe', 'critical', 'high'):
            flags.append(f"Severe allergy: {a['substance']}")
    return flags


def _timeline_lines(ctx):
    lines = []
    for e in ctx['encounters']:
        lines.append(f"{e['date']} Visit: {e['diagnosis'] or e['notes'] or 'consultation'}")
    for a in ctx['admissions']:
        lines.append(f"{a['admitted']} Admission: {a['reason'] or ''} ({a['status']})")
    for r in ctx['referrals']:
        lines.append(f"{r['date']} Referral to {r['to']} ({r['status']})")
    for l in ctx['labs'][:6]:
        lines.append(f"{l['date']} Lab: {l['test']} {l['value']}")
    for im in ctx['imaging'][:3]:
        lines.append(f"{im['date']} Imaging: {im['study']}")
    return sorted([x for x in lines if not x.startswith('None')], reverse=True)[:25]


def _lab_trends(ctx):
    by = {}
    for l in ctx['labs']:
        by.setdefault(l['test'], []).append(l)
    lines = []
    for test, rows in by.items():
        vals = []
        for r in sorted(rows, key=lambda x: (x['date'] or '', x['order_id'])):
            try:
                vals.append((r['date'], float(str(r['value']).replace(',', '.'))))
            except ValueError:
                continue
        if len(vals) >= 2:
            first, last = vals[0][1], vals[-1][1]
            delta = last - first
            direction = 'rising' if delta > 0 else 'falling' if delta < 0 else 'stable'
            lines.append(f"{test}: {first:g} → {last:g} ({direction}, {len(vals)} results)")
        elif vals:
            lines.append(f"{test}: single result {vals[0][1]:g} ({vals[0][0]})")
    return lines


def _draft_hpi(ctx, inputs):
    complaint = inputs.get('complaint') or ctx['why_here'] or 'presenting complaint not recorded'
    parts = [f"Presents with {complaint}."]
    if inputs.get('history'):
        parts.append(inputs['history'])
    d = ctx['demographics']
    if d.get('age') or d.get('gender'):
        parts.insert(0, f"{d.get('age') or ''}-year-old {d.get('gender') or ''} patient.".replace('-year-old  ', ' '))
    if ctx['problems']:
        parts.append('Background: ' + ', '.join(p['description'] for p in ctx['problems'][:5] if p['status'] != 'Resolved') + '.')
    if ctx['medications']:
        parts.append('Current medications: ' + ', '.join(m['name'] for m in ctx['medications'][:8]) + '.')
    if ctx['allergies']:
        parts.append('Allergies: ' + ', '.join(a['substance'] for a in ctx['allergies']) + '.')
    return ' '.join(parts)


def _draft_plan(ctx):
    items = []
    for p in ctx['pending_labs']:
        items.append(f"Await {p['test']} ({p['status']})")
    for p in ctx['pending_imaging']:
        items.append(f"Await {p['study']} ({p['status']})")
    for m in ctx['medications'][:8]:
        items.append(f"Continue {_fmt_med(m)}")
    for f in ctx['follow_ups']:
        items.append(f"Follow-up {f['when']}: {f['reason'] or ''}")
    for t in ctx['tasks'][:5]:
        items.append(f"Open task: {t['title']}")
    return items


def local_sections(action, ctx, inputs):
    """The verified part of every action. Always available, no AI."""
    S = []
    if action in ('patient.summary', 'patient.previsit'):
        S.append(_sec('Why the patient is here', [ctx['why_here'] or 'No reason recorded'], 'text'))
        S.append(_sec('Active problems', [f"{p['description']} ({p['status']})" for p in ctx['problems'] if p['status'] != 'Resolved']))
        S.append(_sec('Allergies', [_fmt_allergy(a) for a in ctx['allergies']] or ['No allergies documented']))
        S.append(_sec('Current medications', [_fmt_med(m) for m in ctx['medications']] or ['No active medications']))
        S.append(_sec('Recent important labs', [_fmt_lab(l) for l in ctx['labs'] if l['abnormal'] or l['critical']][:8]
                      or [_fmt_lab(l) for l in ctx['labs'][:4]] or ['No results']))
        S.append(_sec('Recent imaging', [f"{im['date']} {im['study']}: {im['impression'] or 'no impression'}" for im in ctx['imaging'][:4]]))
        S.append(_sec('Recent encounters', [f"{e['date']}: {e['diagnosis'] or e['notes'] or 'visit'} ({e['status']})" for e in ctx['encounters'][:4]]))
        S.append(_sec('Pending investigations', [f"{p['test']} — {p['status']}" for p in ctx['pending_labs']]
                      + [f"{p['study']} — {p['status']}" for p in ctx['pending_imaging']]))
        S.append(_sec('Outstanding tasks', [f"{t['title']} ({t['priority']}, {t['status']})" for t in ctx['tasks']]))
        S.append(_sec('Follow-up', [f"{f['when']}: {f['reason'] or ''}" for f in ctx['follow_ups']]))
        if action == 'patient.previsit':
            prev = ctx['encounters'][0] if ctx['encounters'] else None
            S.append(_sec('Previous plan', [prev['plan'] if prev and prev['plan'] else 'No previous plan recorded'], 'text'))
            S.append(_sec('Medication changes (last 90 days)', [f"{m['name']} started {m['since']}" for m in ctx['medications'] if m.get('since') and m['since'] >= _days_ago(90)]))
    elif action == 'patient.encounter':
        e = ctx['encounters'][0] if ctx['encounters'] else None
        S.append(_sec('Current encounter', [f"{e['date']} — {e['diagnosis'] or 'no diagnosis yet'}", e['notes'], e['plan']] if e else ['No encounter documented yet'], 'text'))
        S.append(_sec('Latest vitals', [_fmt_vitals(v) for v in ctx['vitals'][:1]] or ['No vitals recorded']))
        S.append(_sec('Orders in flight', [f"{p['test']} ({p['status']})" for p in ctx['pending_labs']] + [f"{p['study']} ({p['status']})" for p in ctx['pending_imaging']]))
    elif action == 'patient.problems':
        S.append(_sec('Active problems', [f"{p['description']}{' [' + p['icd10'] + ']' if p['icd10'] else ''} — {p['status']}{', ' + p['severity'] if p['severity'] else ''}" for p in ctx['problems']] or ['No problems recorded']))
        S.append(_sec('Documented diagnoses', [f"{d['date']} {d['description']}{' [' + d['code'] + ']' if d['code'] else ''}" for d in ctx['diagnoses']]))
    elif action in ('patient.medications', 'safety.medication_review'):
        S.append(_sec('Active medications', [_fmt_med(m) + (f" (since {m['since']})" if m.get('since') else '') for m in ctx['medications']] or ['No active medications']))
        S.append(_sec('Deterministic safety checks (authoritative)', _allergy_conflicts(ctx) + _interaction_findings(ctx) or ['No allergy conflicts, interactions or duplicates detected by the rule engine']))
        S.append(_sec('Relevant results', [_fmt_lab(l) for l in ctx['labs'] if any(k in l['test'].lower() for k in ('creat', 'egfr', 'potassium', 'inr', 'alt', 'ast', 'glucose'))][:6]))
    elif action in ('patient.allergies', 'safety.allergy_review'):
        S.append(_sec('Documented allergies', [_fmt_allergy(a) + (f" [{a['status']}]" if a.get('status') else '') for a in ctx['allergies']] or ['No allergies documented — confirm with the patient']))
        S.append(_sec('Allergy vs current medications (rule engine)', _allergy_conflicts(ctx) or ['No conflicts detected']))
    elif action == 'safety.interactions':
        S.append(_sec('Drug–drug interactions and duplicates (rule engine)', _interaction_findings(ctx) or ['None detected among active medications']))
        S.append(_sec('Active medications', [_fmt_med(m) for m in ctx['medications']]))
    elif action == 'safety.reconciliation':
        r = ctx['reconciliation']
        if r:
            S.append(_sec(f"Last reconciliation ({r['date']}, {r['type']}, {r['status']})", [f"{d['severity']} {d['type']}: {d['description']}{' → ' + d['action'] if d['action'] else ''} [{d['status']}]" for d in r['discrepancies']] or ['No discrepancies recorded']))
        else:
            S.append(_sec('Reconciliation', ['No medication reconciliation on file'], 'text'))
        S.append(_sec('Active medications', [_fmt_med(m) for m in ctx['medications']]))
    elif action == 'patient.timeline':
        S.append(_sec('Timeline (most recent first)', _timeline_lines(ctx)))
    elif action == 'patient.inpatient_daily':
        adm = ctx['current_admission']
        S.append(_sec('Admission', [f"{adm['admitted']} — {adm['ward'] or 'ward'} — {adm['reason'] or ''}"] if adm else ['Patient is not currently admitted'], 'text'))
        S.append(_sec('New results (last 24 h)', [_fmt_lab(l) for l in ctx['labs'] if l['date'] and l['date'] >= _days_ago(1)] + [f"{im['study']}: {im['impression']}" for im in ctx['imaging'] if im['date'] and im['date'] >= _days_ago(1)] or ['None']))
        S.append(_sec('Latest vitals & flags', [_fmt_vitals(v) for v in ctx['vitals'][:2]]))
        S.append(_sec('Nursing notes', [f"{n['date']}: {n['note']}" for n in ctx['nursing_notes']]))
        S.append(_sec('Medication changes (7 days)', [f"{m['name']} started {m['since']}" for m in ctx['medications'] if m.get('since') and m['since'] >= _days_ago(7)]))
        S.append(_sec('Pending orders', [f"{p['test']} ({p['status']})" for p in ctx['pending_labs']] + [f"{p['study']} ({p['status']})" for p in ctx['pending_imaging']]))
        S.append(_sec('Unresolved problems', [p['description'] for p in ctx['problems'] if p['status'] == 'Active']))
        S.append(_sec('Follow-up items', [t['title'] for t in ctx['tasks']] + [f"{f['when']} {f['reason']}" for f in ctx['follow_ups']]))
    elif action == 'dx.labs':
        S.append(_sec('Recent results', [_fmt_lab(l) for l in ctx['labs']] or ['No results']))
        S.append(_sec('Pending', [f"{p['test']} ({p['status']})" for p in ctx['pending_labs']]))
    elif action == 'dx.trends':
        S.append(_sec('Trends (first → last)', _lab_trends(ctx) or ['Not enough results for a trend']))
    elif action == 'dx.radiology':
        S.append(_sec('Reports', [f"{im['date']} {im['study']} [{im['status']}] — Impression: {im['impression'] or 'n/a'}" for im in ctx['imaging']] or ['No reports']))
        S.append(_sec('Pending studies', [f"{p['study']} ({p['status']})" for p in ctx['pending_imaging']]))
    elif action == 'dx.important':
        S.append(_sec('Critical / abnormal results', [_fmt_lab(l) for l in ctx['labs'] if l['abnormal'] or l['critical']] or ['No abnormal results in the recent window']))
        S.append(_sec('Imaging impressions', [f"{im['study']}: {im['impression']}" for im in ctx['imaging'][:3] if im['impression']]))
        S.append(_sec('Deterministic red flags', _red_flags(ctx) or ['None']))
    elif action == 'reasoning.red_flags':
        S.append(_sec('Deterministic red flags (rules)', _red_flags(ctx) or ['No rule-based red flags found']))
        S.append(_sec('Latest vitals', [_fmt_vitals(v) for v in ctx['vitals'][:1]]))
    elif action in ('reasoning.differential', 'reasoning.questions', 'reasoning.investigations'):
        S.append(_sec('Inputs used', [f"Complaint: {inputs.get('complaint') or ctx['why_here'] or 'not provided'}",
                                     f"History: {inputs.get('history') or 'not provided'}",
                                     f"Examination: {inputs.get('exam') or 'not provided'}"], 'text'))
        S.append(_sec('Vitals', [_fmt_vitals(v) for v in ctx['vitals'][:1]] or ['No vitals']))
        S.append(_sec('Relevant results', [_fmt_lab(l) for l in ctx['labs'][:6]] + [f"{im['study']}: {im['impression']}" for im in ctx['imaging'][:2]]))
        S.append(_sec('Active problems & medications', [p['description'] for p in ctx['problems'][:6]] + [m['name'] for m in ctx['medications'][:8]]))
        S.append(_sec('Deterministic red flags', _red_flags(ctx) or ['None']))
    elif action == 'doc.hpi':
        S.append(_sec('HPI draft (from chart)', [_draft_hpi(ctx, inputs)], 'text'))
    elif action == 'doc.structure':
        S.append(_sec('S — Subjective', [inputs.get('complaint') or ctx['why_here'] or '', inputs.get('history') or ''], 'text'))
        S.append(_sec('O — Objective', [_fmt_vitals(v) for v in ctx['vitals'][:1]] + [inputs.get('exam') or ''] + [_fmt_lab(l) for l in ctx['labs'][:4]]))
        S.append(_sec('A — Assessment', [p['description'] for p in ctx['problems'][:5]] or [inputs.get('assessment') or '']))
        S.append(_sec('P — Plan', _draft_plan(ctx)))
    elif action == 'doc.assessment':
        S.append(_sec('Assessment draft', [f"Active: {p['description']}" for p in ctx['problems'] if p['status'] == 'Active'] + ([f"Working impression: {inputs['assessment']}"] if inputs.get('assessment') else [])))
    elif action == 'doc.plan':
        S.append(_sec('Plan draft (from open orders, medications, follow-ups)', _draft_plan(ctx) or ['Nothing pending']))
    elif action in ('doc.encounter_summary', 'doc.postvisit'):
        e = ctx['encounters'][0] if ctx['encounters'] else None
        S.append(_sec('Encounter', [f"{e['date']}: {e['diagnosis'] or ''}", e['notes'], e['plan']] if e else ['No encounter documented'], 'text'))
        S.append(_sec('Orders & prescriptions', [f"{p['test']}" for p in ctx['pending_labs']] + [f"{p['study']}" for p in ctx['pending_imaging']] + [_fmt_med(m) for m in ctx['medications'][:6]]))
        S.append(_sec('Follow-up', [f"{f['when']}: {f['reason']}" for f in ctx['follow_ups']] or ['No follow-up scheduled']))
    elif action == 'doc.followup':
        S.append(_sec('Follow-up draft', [f"Review in clinic {f['when']} for {f['reason'] or 'review'}" for f in ctx['follow_ups']] or ['Book a follow-up after pending results return'] , 'text'))
        S.append(_sec('Return precautions (generic)', ['Worsening symptoms', 'New chest pain, breathlessness or confusion', 'Fever not settling', 'Any severe or unexpected reaction to medication']))
    elif action == 'doc.discharge':
        adm = ctx['admissions'][0] if ctx['admissions'] else None
        S.append(_sec('Admission', [f"{adm['admitted']} → {adm['discharged'] or 'in hospital'} — {adm['reason'] or ''} ({adm['ward'] or ''})"] if adm else ['No admission on file'], 'text'))
        S.append(_sec('Diagnoses', [p['description'] for p in ctx['problems'][:6]] + [d['description'] for d in ctx['diagnoses'][:4]]))
        S.append(_sec('Important results', [_fmt_lab(l) for l in ctx['labs'] if l['abnormal'] or l['critical']][:8] + [f"{im['study']}: {im['impression']}" for im in ctx['imaging'][:3]]))
        S.append(_sec('Discharge medications (current active list)', [_fmt_med(m) for m in ctx['medications']]))
        S.append(_sec('Follow-up', [f"{f['when']}: {f['reason']}" for f in ctx['follow_ups']] + [t['title'] for t in ctx['tasks']]))
    elif action == 'doc.referral':
        S.append(_sec('Referral summary', [f"Reason: {inputs.get('reason') or ctx['why_here'] or ''}"] + [f"Problems: {', '.join(p['description'] for p in ctx['problems'][:5])}"] + [f"Medications: {', '.join(m['name'] for m in ctx['medications'][:8])}"] + [f"Allergies: {', '.join(a['substance'] for a in ctx['allergies']) or 'none documented'}"] + [f"Key results: {'; '.join(_fmt_lab(l) for l in ctx['labs'][:4])}"], 'text'))
    elif action == 'comm.summary':
        S.append(_sec('Plain-language points (from chart)', [f"You came in for: {ctx['why_here'] or 'a review'}"] + [f"Condition being managed: {p['description']}" for p in ctx['problems'][:4]] + [f"Medicine: {m['name']} {m['frequency'] or ''}" for m in ctx['medications'][:6]] + [f"Next step: {f['reason']} on {f['when']}" for f in ctx['follow_ups'][:2]]))
    elif action == 'comm.education':
        items = []
        for m in ctx['medications'][:6]:
            info = explain_medication(m['name'])
            if info:
                items.append(f"{m['name']}: {info['purpose']} {info['instructions']} {info['precautions']}")
        for l in ctx['labs'][:4]:
            info = explain_test(l['test'])
            if info:
                items.append(f"{l['test']}: {info['what']}")
        S.append(_sec('Education from the reviewed library', items or ['No library entry matched; use the AI draft or write instructions manually']))
        S.append(_sec('Questions patients often ask', QUESTIONS_TO_ASK))
    elif action == 'comm.terms':
        blob = ' '.join([inputs.get('text') or ''] + [e['diagnosis'] or '' for e in ctx['encounters'][:2]] + [im['impression'] or '' for im in ctx['imaging'][:2]] + [p['description'] for p in ctx['problems'][:5]])
        terms = find_terms(blob)
        S.append(_sec('Glossary matches', [f"{t}: {x}" for t, x in terms.items()] or ['No glossary term found in the current text']))
    return S


def _days_ago(n):
    from datetime import timedelta
    from app.utils import utcnow
    return (utcnow() - timedelta(days=n)).strftime('%Y-%m-%d')


def _fmt_vitals(v):
    if not v:
        return ''
    parts = [f"{v['date']}"]
    if v.get('bp'): parts.append(f"BP {v['bp']}")
    if v.get('hr'): parts.append(f"HR {v['hr']}")
    if v.get('rr'): parts.append(f"RR {v['rr']}")
    if v.get('temp'): parts.append(f"T {v['temp']}")
    if v.get('spo2'): parts.append(f"SpO2 {v['spo2']}%")
    if v.get('pain') is not None: parts.append(f"pain {v['pain']}/10")
    if v.get('glucose'): parts.append(f"glucose {v['glucose']}")
    if v.get('flags'): parts.append('FLAGS: ' + '; '.join(v['flags']))
    return ' · '.join(parts)


# ---------------------------------------------------------------------------
# AI prompts (data blocks only; instructions are ours)
# ---------------------------------------------------------------------------
_TASKS = {
    'patient.summary': 'Write a concise clinical summary (max 8 bullet points) for the treating physician: why here, active problems, key medications, notable results, pending items.',
    'patient.previsit': 'Write a pre-visit briefing (max 8 bullets): what changed since the last visit, outstanding results, medication changes, what to address today.',
    'patient.encounter': 'Summarise the current encounter in 5 bullets.',
    'patient.problems': 'Group and prioritise the active problems in 5 bullets; note any problem lacking a plan.',
    'patient.medications': 'Review the medication list: purpose of each, possible issues to check (renal dosing, duplicates, monitoring). Max 8 bullets. Do not recommend stopping anything; suggest what to verify.',
    'patient.allergies': 'Summarise allergy relevance to the current medications in 4 bullets.',
    'patient.timeline': 'Narrate the timeline in 6 bullets from oldest to newest.',
    'patient.inpatient_daily': 'Write a ward-round daily summary: overnight events, new results, alerts, medication changes, pending orders, unresolved problems, follow-up items. Max 10 bullets.',
    'dx.labs': 'Summarise the lab results for a physician in 6 bullets: which are abnormal, what pattern they suggest, what to recheck.',
    'dx.trends': 'Explain the lab trends in 5 bullets, naming direction and clinical relevance.',
    'dx.radiology': 'Summarise the radiology reports in 5 bullets, flag any urgent language.',
    'dx.important': 'List the 5 most important results to act on and why.',
    'reasoning.differential': 'Return JSON: {"considerations":[{"name":..., "supporting":[...], "contradicting":[...]}], "red_flags":[...], "questions":[...], "investigations":[...]}. Max 5 considerations. This is differential SUPPORT, not a diagnosis.',
    'reasoning.red_flags': 'List red flags a physician should exclude given this presentation (max 6 bullets).',
    'reasoning.questions': 'Suggest 8 focused history questions the physician could ask next.',
    'reasoning.investigations': 'Suggest investigations to consider, grouped as first-line and if-indicated (max 8 bullets), each with one-line rationale.',
    'doc.hpi': 'Rewrite the HPI draft into a fluent, structured HPI paragraph (onset, location, duration, character, associated symptoms, timing, exacerbating/relieving, severity). Keep facts; mark unknowns as [not documented].',
    'doc.structure': 'Produce a SOAP note using only the supplied data; mark missing parts as [to complete].',
    'doc.assessment': 'Draft an assessment paragraph from the problems and data. No new diagnoses.',
    'doc.plan': 'Draft a numbered plan from pending orders, medications and follow-ups; add monitoring reminders where appropriate.',
    'doc.encounter_summary': 'Write a 5-line encounter summary.',
    'doc.followup': 'Draft follow-up instructions (interval, what to monitor, return precautions) in 6 bullets.',
    'doc.postvisit': 'Draft (1) a visit summary for the record, (2) patient instructions in plain language, (3) follow-up instructions. Keep each section short.',
    'doc.discharge': 'Draft a discharge summary with headings: Admission reason, Hospital course, Diagnoses, Key results, Procedures, Discharge medications, Follow-up. Use only supplied data; mark gaps as [to complete].',
    'doc.referral': 'Write a concise referral letter body (reason, relevant history, medications, allergies, key results, specific question).',
    'safety.medication_review': 'Explain the rule-engine findings and add monitoring considerations. Max 6 bullets. Never override the rule engine; do not tell the physician to stop a drug.',
    'safety.allergy_review': 'Explain any allergy–medication relevance in 4 bullets.',
    'safety.interactions': 'Explain the listed interactions and how they are usually managed (max 6 bullets).',
    'safety.reconciliation': 'Summarise the reconciliation discrepancies and what each needs (max 6 bullets).',
    'comm.summary': 'Write a patient-friendly summary (6 short sentences, no jargon, reading age 12). Do not add advice to change treatment.',
    'comm.education': 'Write patient education for the conditions and medicines listed: what it is, what to do, warning signs, when to contact the clinic. Max 10 short bullets.',
    'comm.terms': 'Explain each medical term in the text in one plain sentence each.',
}

_JSON_ACTIONS = {'reasoning.differential'}


def _prompt(action, ctx, inputs):
    task = _TASKS.get(action, 'Summarise the data for a physician in 5 bullets.')
    data = json.dumps(context_fingerprint(ctx), ensure_ascii=False, default=str)
    user = json.dumps({k: platform.sanitize_text(v, 1500) for k, v in (inputs or {}).items() if v}, ensure_ascii=False)
    return (f"TASK: {task}\nAnswer in English unless the data is Arabic; plain text bullets, no markdown headers, no HTML.\n"
            f"{platform.data_block('chart', data, 12000)}\n"
            f"{platform.data_block('clinician_inputs', user, 4000)}")


def run_action(action, patient, inputs=None, use_ai=True, role_names=None):
    """Run one Copilot action for a patient. Never raises for AI reasons."""
    inputs = {k: platform.sanitize_text(v, 2000) for k, v in (inputs or {}).items()}
    spec = ACTIONS.get(action)
    if spec is None:
        raise KeyError(action)
    ctx = build_context(patient)
    verified = local_sections(action, ctx, inputs)
    out = {'action': action, 'label': spec[1], 'label_ar': spec[2], 'group': spec[0],
           'verified': verified, 'ai': None, 'disclaimer': DISCLAIMER,
           'status': platform.status()}
    if not use_ai:
        return out
    result = platform.run_ai(
        f'copilot.{action}', patient.id,
        {'fp': context_fingerprint(ctx), 'inputs': inputs},
        _prompt(action, ctx, inputs), heavy=spec[3],
        json_mode=action in _JSON_ACTIONS,
        max_tokens=900 if spec[3] else 600)
    out['ai'] = {k: result.get(k) for k in ('status', 'provider', 'text', 'data', 'message',
                                             'usage_id', 'cached', 'injection_flag')}
    return out
