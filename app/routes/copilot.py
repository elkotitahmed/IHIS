"""AI Clinical Copilot, predictive AI pages, smart autocomplete, diagnosis
lookup, medication safety and result review endpoints.

All AI is optional, budgeted, cached, audited and permission/patient-aware.
Deterministic clinical data is always returned first; AI enrichment is an
explicit action and never writes to the chart.
"""
from flask import (Blueprint, abort, jsonify, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required

from app import db, limiter
from app.access import accessible_patient_ids, has_need_to_know, require_patient_access
from app.models import (LabOrder, LabResult, Medication, Patient, RadiologyOrder,
                        RadiologyReport)
from app.permissions import AI_USE
from app.routes.decorators import permissions_required, roles_required
from app.services import terminology
from app.services.ai import copilot as copilot_svc
from app.services.ai import platform
from app.services.ai.clinical_context import build_context, context_fingerprint
from app.services.ai.patient_education import explain_test
from app.services.patient_safety import patient_safety_context

copilot_bp = Blueprint('copilot', __name__, url_prefix='/ai/copilot')

STAFF_AI_ROLES = ('Doctor', 'Nurse', 'Pharmacist', 'Dentist', 'Physiotherapist',
                  'Radiologist', 'RadiologyTechnician', 'Admin', 'SuperAdmin')


def _roles():
    return [r.name for r in current_user.roles]


def _patient_or_403(patient_id):
    patient = db.session.get(Patient, int(patient_id)) if patient_id else None
    if patient is None:
        abort(404)
    if not has_need_to_know(patient):
        abort(403)
    return patient


def _json_error(message, status=400):
    return jsonify({'ok': False, 'error': message}), status


# ---------------------------------------------------------------------------
# Panel + actions
# ---------------------------------------------------------------------------
@copilot_bp.route('/status')
@login_required
def status():
    return jsonify(platform.status())


@copilot_bp.route('/panel')
@login_required
@permissions_required(AI_USE)
def panel():
    roles = _roles()
    pid = request.args.get('patient_id', type=int)
    patient = None
    if pid:
        p = db.session.get(Patient, pid)
        if p is not None and has_need_to_know(p):
            patient = {'id': p.id, 'label': p.user.full_name if p.user else f'Patient #{p.id}',
                       'mrn': p.mrn}
    recent = []
    if patient is None:
        ids = sorted(accessible_patient_ids(current_user), reverse=True)[:8]
        for p in Patient.query.filter(Patient.id.in_(ids or [-1])).all():
            recent.append({'id': p.id, 'label': p.user.full_name if p.user else f'Patient #{p.id}',
                           'mrn': p.mrn})
    return jsonify({'ok': True, 'status': platform.status(), 'patient': patient,
                    'recent_patients': recent, 'groups': copilot_svc.catalogue(roles),
                    'disclaimer': copilot_svc.DISCLAIMER})


@copilot_bp.route('/run', methods=['POST'])
@login_required
@permissions_required(AI_USE)
@limiter.limit('30/minute')
def run():
    body = request.get_json(silent=True) or {}
    action = (body.get('action') or '').strip()
    if not copilot_svc.allowed(action, _roles()):
        return _json_error('This action is not available for your role.', 403)
    patient = _patient_or_403(body.get('patient_id'))
    inputs = body.get('inputs') if isinstance(body.get('inputs'), dict) else {}
    inputs = {str(k)[:40]: str(v)[:4000] for k, v in inputs.items() if isinstance(v, (str, int, float))}
    use_ai = bool(body.get('use_ai', True))
    out = copilot_svc.run_action(action, patient, inputs=inputs, use_ai=use_ai, role_names=_roles())
    platform.record_usage(f'copilot.{action}', 'viewed', provider='local', patient_id=patient.id,
                          commit=True)
    return jsonify({'ok': True, **out})


@copilot_bp.route('/feedback', methods=['POST'])
@login_required
@permissions_required(AI_USE)
def feedback():
    body = request.get_json(silent=True) or {}
    try:
        ok = platform.mark_feedback(int(body.get('usage_id')), bool(body.get('accepted')))
    except (TypeError, ValueError):
        ok = False
    return jsonify({'ok': ok})


@copilot_bp.route('/priority', methods=['POST'])
@login_required
@permissions_required(AI_USE)
@limiter.limit('10/minute')
def priority():
    """'What needs my attention first?' — deterministic ranking, optional AI narrative."""
    from app.services.inbox_priority import build_items, ai_priority_narrative
    body = request.get_json(silent=True) or {}
    items = build_items(current_user, accessible_patient_ids(current_user))
    payload = [{k: (v.strftime('%Y-%m-%d %H:%M') if k == 'when' and v else v) for k, v in i.items()} for i in items]
    ai = None
    if body.get('use_ai', True) and items:
        res = ai_priority_narrative(items, current_user.id)
        ai = {k: res.get(k) for k in ('status', 'text', 'message', 'usage_id', 'cached')}
    return jsonify({'ok': True, 'items': payload, 'ai': ai,
                    'note': 'Ranking is rule-based and authoritative; AI only narrates.'})


# ---------------------------------------------------------------------------
# Smart autocomplete (local first, AI optional and debounced by the client)
# ---------------------------------------------------------------------------
@copilot_bp.route('/autocomplete')
@login_required
def autocomplete():
    field = (request.args.get('field') or 'hpi')[:20]
    q = (request.args.get('q') or '')[:120]
    local = terminology.suggest_phrases(field, q)
    st = platform.status()
    return jsonify({'ok': True, 'field': field, 'suggestions': local,
                    'ai_enabled': st['state'] == 'READY' and platform.settings()['autocomplete']})


@copilot_bp.route('/autocomplete/ai', methods=['POST'])
@login_required
@permissions_required(AI_USE)
@limiter.limit('20/minute')
def autocomplete_ai():
    body = request.get_json(silent=True) or {}
    field = (body.get('field') or 'hpi')[:20]
    text = (body.get('text') or '')[-400:]
    if len(text.strip()) < 12:
        return jsonify({'ok': True, 'suggestion': None})
    pid = body.get('patient_id')
    patient_id = None
    if pid:
        p = db.session.get(Patient, int(pid))
        if p is not None and has_need_to_know(p):
            patient_id = p.id
    prompt = (f"TASK: Continue this clinical {field} note with at most 12 words that plausibly follow. "
              f"Return only the continuation text, no quotes, no HTML.\n"
              f"{platform.data_block('note_tail', text, 400)}")
    res = platform.run_ai(f'autocomplete.{field}', patient_id, {'tail': text[-200:]}, prompt,
                          autocomplete=True, max_tokens=40, temperature=0.2)
    suggestion = (res.get('text') or '').strip().split('\n')[0][:120] if res['status'] in ('ok', 'cached') else None
    return jsonify({'ok': True, 'suggestion': suggestion or None, 'status': res['status'],
                    'usage_id': res.get('usage_id')})


# ---------------------------------------------------------------------------
# Smart diagnosis entry
# ---------------------------------------------------------------------------
@copilot_bp.route('/diagnosis-lookup')
@login_required
def diagnosis_lookup():
    q = (request.args.get('q') or '')[:80]
    return jsonify({'ok': True, **terminology.diagnosis_lookup(q, current_user.id)})


@copilot_bp.route('/diagnosis-suggest', methods=['POST'])
@login_required
@roles_required('Doctor', 'Dentist', 'Admin', 'SuperAdmin')
@permissions_required(AI_USE)
@limiter.limit('10/minute')
def diagnosis_suggest():
    body = request.get_json(silent=True) or {}
    text = (body.get('text') or '')[:1500]
    if len(text.strip()) < 4:
        return jsonify({'ok': True, 'suggestions': [], 'status': 'skipped'})
    patient_id = None
    if body.get('patient_id'):
        p = db.session.get(Patient, int(body['patient_id']))
        if p is not None and has_need_to_know(p):
            patient_id = p.id
    local = terminology.search_icd(text.split()[0] if text.split() else text)
    prompt = ('TASK: Return JSON {"suggestions":[{"code":"ICD-10","term":"...","why":"one clause"}]} with at most 5 '
              'candidate ICD-10 codes matching the clinician text. The clinician chooses; never assert a diagnosis.\n'
              f"{platform.data_block('clinician_text', text, 1500)}")
    res = platform.run_ai('diagnosis.suggest', patient_id, {'text': text}, prompt, json_mode=True,
                          max_tokens=300)
    ai = []
    if res['status'] in ('ok', 'cached') and isinstance(res.get('data'), dict):
        for s in (res['data'].get('suggestions') or [])[:5]:
            if isinstance(s, dict) and s.get('term'):
                ai.append({'code': str(s.get('code') or '')[:12], 'term': str(s['term'])[:150],
                           'why': str(s.get('why') or '')[:200]})
    return jsonify({'ok': True, 'local': local, 'suggestions': ai, 'status': res['status'],
                    'message': res.get('message'), 'usage_id': res.get('usage_id')})


# ---------------------------------------------------------------------------
# Medication safety (deterministic first; AI review is a separate explicit call)
# ---------------------------------------------------------------------------
def medication_safety(patient, medication):
    from app.models import Allergy, DrugInteraction, Prescription, PrescriptionItem
    out = {'allergy': [], 'interactions': [], 'duplicates': [], 'warnings': [], 'level': 'OK'}
    name = (medication.generic_name or '').lower()
    brand = (medication.brand_name or '').lower()
    allergies = [a for a in Allergy.query.filter_by(patient_id=patient.id).all()
                 if (a.status or 'Active') != 'Inactive']
    free_text = (patient.allergies or '').lower()
    for a in allergies:
        reason = copilot_svc.allergy_conflict(a.substance, medication.generic_name, medication.brand_name)
        if reason:
            out['allergy'].append(reason[:1].upper() + reason[1:]
                                  + (f" ({a.reaction})" if a.reaction else '')
                                  + (f" — {a.severity}" if a.severity else ''))
    if free_text and name and (name in free_text or (brand and brand in free_text)):
        out['allergy'].append('Free-text allergy field mentions this medication')
    active = (PrescriptionItem.query.join(Prescription, PrescriptionItem.prescription_id == Prescription.id)
              .filter(Prescription.patient_id == patient.id, Prescription.status == 'Active',
                      PrescriptionItem.status != 'Cancelled').all())
    active_ids = {it.medication_id for it in active if it.medication_id}
    names = {it.medication_id: (it.medication.generic_name if it.medication else '?') for it in active}
    if medication.id in active_ids:
        out['duplicates'].append(f"{medication.generic_name} is already on an active prescription")
    for it in active:
        if it.medication and it.medication.id != medication.id and \
                (it.medication.generic_name or '').lower() == name:
            out['duplicates'].append(f"Same generic already active: {it.medication.generic_name}")
    if active_ids:
        rows = (DrugInteraction.query.filter(
            db.or_(db.and_(DrugInteraction.medication_a_id == medication.id,
                           DrugInteraction.medication_b_id.in_(active_ids)),
                   db.and_(DrugInteraction.medication_b_id == medication.id,
                           DrugInteraction.medication_a_id.in_(active_ids)))).all())
        for r in rows:
            other = r.medication_b_id if r.medication_a_id == medication.id else r.medication_a_id
            out['interactions'].append({'with': names.get(other, f'#{other}'),
                                        'severity': r.severity or 'Moderate',
                                        'description': r.description or '',
                                        'management': getattr(r, 'management', None) or ''})
    if medication.contraindications:
        out['warnings'].append(f"Contraindications: {medication.contraindications}")
    if medication.side_effects:
        out['warnings'].append(f"Common side effects: {medication.side_effects}")
    sev = {i['severity'].lower() for i in out['interactions']}
    if out['allergy'] or 'contraindicated' in sev:
        out['level'] = 'CRITICAL'
    elif 'major' in sev or 'high' in sev or 'severe' in sev:
        out['level'] = 'HIGH'
    elif out['interactions'] or out['duplicates']:
        out['level'] = 'MODERATE'
    return out


@copilot_bp.route('/medication-safety')
@login_required
@permissions_required(AI_USE)
def medication_safety_view():
    patient = _patient_or_403(request.args.get('patient_id', type=int))
    med = db.session.get(Medication, request.args.get('medication_id', type=int) or 0)
    if med is None:
        return _json_error('Medication not found.', 404)
    out = medication_safety(patient, med)
    return jsonify({'ok': True, 'medication': med.generic_name, **out})


@copilot_bp.route('/medication-review', methods=['POST'])
@login_required
@permissions_required(AI_USE)
@limiter.limit('10/minute')
def medication_review_ai():
    body = request.get_json(silent=True) or {}
    patient = _patient_or_403(body.get('patient_id'))
    med = db.session.get(Medication, int(body.get('medication_id') or 0))
    if med is None:
        return _json_error('Medication not found.', 404)
    safety = medication_safety(patient, med)
    ctx = build_context(patient, labs=8, imaging=2, encounters=2)
    prompt = ('TASK: The physician is about to prescribe the medication below. Given the deterministic safety '
              'findings (authoritative) and the chart, list in max 6 bullets what to verify before prescribing '
              '(dose adjustment for renal/hepatic function, monitoring, timing, counselling). Do not contradict '
              'the safety findings and do not tell the physician not to prescribe.\n'
              f"Medication: {med.generic_name}\n{platform.data_block('safety', str(safety), 3000)}\n"
              f"{platform.data_block('chart', str(context_fingerprint(ctx)), 8000)}")
    res = platform.run_ai('medication.review', patient.id, {'med': med.id, 'fp': context_fingerprint(ctx)},
                          prompt, max_tokens=450)
    return jsonify({'ok': True, 'safety': safety, 'ai': {k: res.get(k) for k in
                                                          ('status', 'text', 'message', 'usage_id', 'cached')}})


# ---------------------------------------------------------------------------
# Smart result review (explicit "Analyze with AI")
# ---------------------------------------------------------------------------
def _lab_review_local(patient, order):
    r = order.result
    name = order.test.test_name if order.test else 'Lab test'
    ctx = build_context(patient, labs=30, imaging=0, encounters=1)
    series = [l for l in ctx['labs'] if l['test'] == name]
    series.sort(key=lambda x: (x['date'] or '', x['order_id']))
    trend = None
    if len(series) >= 2:
        try:
            first, last = float(series[0]['value']), float(series[-1]['value'])
            trend = f"{first:g} → {last:g} over {len(series)} results ({'rising' if last > first else 'falling' if last < first else 'stable'})"
        except ValueError:
            trend = None
    info = explain_test(name)
    sections = [
        {'title': 'Result', 'items': [f"{name}: {r.result_value} {r.result_unit or ''}"
                                      + (f" (ref {order.test.normal_range})" if order.test and order.test.normal_range else '')
                                      + (' — CRITICAL' if r.is_critical else ' — abnormal' if r.is_abnormal else ' — within range')]},
        {'title': 'Trend', 'items': [trend or 'No previous result of this test']},
        {'title': 'Other abnormal results', 'items': [f"{l['date']} {l['test']}: {l['value']} {l['unit'] or ''}"
                                                       for l in ctx['labs'] if (l['abnormal'] or l['critical']) and l['test'] != name][:6] or ['None']},
        {'title': 'Deterministic red flags', 'items': ([f"Critical value flagged by threshold rules"] if r.is_critical else []) or ['None']},
    ]
    if info:
        sections.append({'title': 'About this test', 'items': [info['what'], info['means']]})
    return sections, ctx, name, series


@copilot_bp.route('/result-review', methods=['POST'])
@login_required
@permissions_required(AI_USE)
@limiter.limit('15/minute')
def result_review():
    body = request.get_json(silent=True) or {}
    kind = body.get('kind')
    use_ai = bool(body.get('use_ai', True))
    rid = int(body.get('id') or 0)
    if kind == 'lab':
        order = db.session.get(LabOrder, rid)
        if order is None or order.result is None:
            return _json_error('Result not found.', 404)
        patient = _patient_or_403(order.patient_id)
        sections, ctx, name, series = _lab_review_local(patient, order)
        ai = None
        if use_ai:
            prompt = ('TASK: For the physician, review this lab result: abnormal values, trend, important changes, '
                      'possible clinical significance, what to consider rechecking. Max 6 bullets, plain text. '
                      'Deterministic flags are authoritative.\n'
                      f"{platform.data_block('result', f'{name}: {order.result.result_value} {order.result.result_unit or ''}; critical={order.result.is_critical}; abnormal={order.result.is_abnormal}', 500)}\n"
                      f"{platform.data_block('series', str(series), 3000)}\n"
                      f"{platform.data_block('chart', str(context_fingerprint(ctx)), 8000)}")
            res = platform.run_ai('result.lab', patient.id, {'order': order.id, 'fp': context_fingerprint(ctx)},
                                  prompt, max_tokens=450)
            ai = {k: res.get(k) for k in ('status', 'text', 'message', 'usage_id', 'cached')}
        return jsonify({'ok': True, 'kind': 'lab', 'title': f'{name} result', 'verified': sections, 'ai': ai,
                        'disclaimer': copilot_svc.DISCLAIMER})
    if kind == 'radiology':
        order = db.session.get(RadiologyOrder, rid)
        if order is None or order.report is None:
            return _json_error('Report not found.', 404)
        patient = _patient_or_403(order.patient_id)
        from app.services import radiology_critical as rc
        evaluation = rc.evaluate_report(order, order.report)
        prior = [im for im in build_context(patient, labs=0, imaging=6, encounters=0)['imaging']
                 if im['order_id'] != order.id]
        sections = [
            {'title': 'Radiologist report', 'items': [f"Impression: {order.report.impression or 'n/a'}",
                                                     f"Findings: {(order.report.findings or 'n/a')[:600]}",
                                                     f"Status: {order.report.status}"]},
            {'title': 'Potential important findings (rules + local model)',
             'items': [f"{f['finding']} — {f['severity']} — {f['action']}" for f in evaluation['findings']] or ['Nothing flagged']},
            {'title': 'Comparison', 'items': [f"{im['date']} {im['study']}: {im['impression']}" for im in prior[:3]] or ['No prior imaging']},
        ]
        ai = None
        if use_ai:
            res = rc.ai_explain(order, order.report, evaluation, patient_id=patient.id)
            ai = {k: res.get(k) for k in ('status', 'text', 'message', 'usage_id', 'cached')}
        return jsonify({'ok': True, 'kind': 'radiology', 'title': 'Radiology report review',
                        'verified': sections, 'evaluation': {'severity': evaluation['severity'],
                                                             'critical': evaluation['critical'],
                                                             'findings': evaluation['findings']},
                        'ai': ai, 'disclaimer': copilot_svc.DISCLAIMER})
    return _json_error('Unknown result kind.')


# ---------------------------------------------------------------------------
# Predictive AI pages
# ---------------------------------------------------------------------------
@copilot_bp.route('/radiology')
@login_required
@roles_required('Doctor', 'Radiologist', 'RadiologyTechnician', 'Admin', 'SuperAdmin')
@permissions_required(AI_USE)
def radiology_ai():
    ids = accessible_patient_ids(current_user)
    reports = (RadiologyReport.query.join(RadiologyOrder, RadiologyReport.order_id == RadiologyOrder.id)
               .filter(RadiologyOrder.patient_id.in_(sorted(ids) if ids else [-1]))
               .order_by(RadiologyReport.report_date.desc()).limit(40).all())
    from app.services import radiology_critical as rc
    rows = []
    for rep in reports:
        rows.append({'report': rep, 'order': rep.order, 'alert': rc.alert_for_report(rep.id)})
    return render_template('ai/radiology_ai.html', title='Radiology AI', rows=rows,
                           predictive=copilot_svc.predictive_catalogue(_roles()),
                           ai_status=platform.status())


@copilot_bp.route('/radiology/<int:order_id>')
@login_required
@roles_required('Doctor', 'Radiologist', 'RadiologyTechnician', 'Admin', 'SuperAdmin')
@permissions_required(AI_USE)
def radiology_ai_report(order_id):
    order = db.session.get(RadiologyOrder, order_id)
    if order is None:
        abort(404)
    require_patient_access(order.patient)
    from app.services import radiology_critical as rc
    report = order.report
    evaluation = rc.evaluate_report(order, report) if report else None
    alert = rc.alert_for_report(report.id) if report else None
    task = rc.open_task_for_alert(alert.id) if alert else None
    return render_template('ai/radiology_ai_report.html', title='Radiology AI Assistance',
                           order=order, report=report, evaluation=evaluation, alert=alert, task=task,
                           patient=order.patient, ai_status=platform.status(),
                           **patient_safety_context(order.patient_id))


@copilot_bp.route('/dentistry')
@login_required
@roles_required('Dentist', 'Doctor', 'Admin', 'SuperAdmin')
@permissions_required(AI_USE)
def dentistry_ai():
    pid = request.args.get('patient_id', type=int)
    patient = _patient_or_403(pid) if pid else None
    ids = sorted(accessible_patient_ids(current_user), reverse=True)[:30]
    patients = Patient.query.filter(Patient.id.in_(ids or [-1])).all() if patient is None else []
    summary = None
    if patient is not None:
        from app.services.ai.dentistry_ai import dental_summary
        summary = dental_summary(patient)
    from app.services.ai.tooth_segmentation import tooth_model_available
    return render_template('ai/dentistry_ai.html', title='Dentistry AI', patient=patient,
                           patients=patients, summary=summary, ai_status=platform.status(),
                           tooth_available=tooth_model_available(),
                           **(patient_safety_context(patient.id) if patient else {}))


@copilot_bp.route('/dentistry/<int:patient_id>/run', methods=['POST'])
@login_required
@roles_required('Dentist', 'Doctor', 'Admin', 'SuperAdmin')
@permissions_required(AI_USE)
@limiter.limit('10/minute')
def dentistry_ai_run(patient_id):
    patient = _patient_or_403(patient_id)
    body = request.get_json(silent=True) or {}
    from app.services.ai.dentistry_ai import run_dental_action
    action = (body.get('action') or 'summary')[:30]
    out = run_dental_action(action, patient, use_ai=bool(body.get('use_ai', True)))
    return jsonify({'ok': True, **out})


@copilot_bp.route('/dermatology')
@login_required
def dermatology_ai():
    return redirect(url_for('ai.skin_lesion_detection'))
