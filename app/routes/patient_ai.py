"""Safe patient-facing AI.

Every endpoint resolves the *logged-in* patient (or a supervisor's labelled
preview) and can only read that patient's own authorised data. Static,
clinician-reviewed education is served first; Gemini adds plain-language
help only when the platform allows it. The assistant never diagnoses,
prescribes, discontinues medication or overrides clinician instructions.
"""
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app import db, limiter
from app.models import Appointment, LabOrder, Medication, Prescription, PrescriptionItem
from app.routes.decorators import roles_required
from app.services.ai import platform
from app.services.ai.patient_education import (QUESTIONS_TO_ASK, explain_medication,
                                               explain_term, explain_test, find_terms,
                                               preparation_for)

patient_ai_bp = Blueprint('patient_ai', __name__, url_prefix='/ai/patient')

PATIENT_GUARD = (
    "You are helping a PATIENT understand their own health information in plain, calm language "
    "(reading age 12). Never give a diagnosis, never tell them to start, stop or change any "
    "medicine or treatment, never contradict their clinician. Always end with when to contact "
    "their physician or emergency services. Max 8 short sentences or bullets."
)
SAFETY_FOOTER = ('This explanation is general information to help you talk with your physician. '
                 'It is not a diagnosis and does not change your treatment. If you feel unwell or '
                 'your symptoms get worse, contact your physician or emergency services.')


def _patient():
    from app.routes.patient import _current_patient
    return _current_patient()


def _own_or_none(query, patient):
    obj = query
    if obj is None or getattr(obj, 'patient_id', None) != patient.id:
        return None
    return obj


def _ai(feature, patient, context, prompt, max_tokens=400):
    res = platform.run_ai(feature, patient.id, context, prompt, system=PATIENT_GUARD,
                          max_tokens=max_tokens, temperature=0.2)
    return {k: res.get(k) for k in ('status', 'text', 'message', 'usage_id', 'cached')}


@patient_ai_bp.route('/explain-result', methods=['POST'])
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
@limiter.limit('10/minute')
def explain_result():
    patient = _patient()
    if patient is None:
        return jsonify({'ok': False, 'error': 'No patient profile.'}), 404
    body = request.get_json(silent=True) or {}
    order = _own_or_none(db.session.get(LabOrder, int(body.get('order_id') or 0)), patient)
    if order is None:
        return jsonify({'ok': False, 'error': 'Result not found.'}), 404
    name = order.test.test_name if order.test else 'Test'
    r = order.result
    if r is None or r.status not in ('Verified', 'Locked', 'Finalized'):
        return jsonify({'ok': True, 'title': name, 'sections': [
            {'title': 'Not ready yet', 'items': ['This result has not been released by the laboratory yet. Your physician reviews results before they are explained.']}],
            'ai': None, 'footer': SAFETY_FOOTER})
    info = explain_test(name)
    sections = []
    if info:
        sections += [{'title': 'What the test is', 'items': [info['what']]},
                     {'title': 'What this result generally means', 'items': [info['means']]},
                     {'title': 'What may be important', 'items': [info['important']]}]
    flag = 'outside the reference range' if (r.is_abnormal or r.is_critical) else 'within the reference range'
    sections.append({'title': 'Your value', 'items': [f"{name}: {r.result_value} {r.result_unit or ''} — {flag}"
                                                      + (f" (reference {order.test.normal_range})" if order.test and order.test.normal_range else '')]})
    sections.append({'title': 'What to discuss with your physician', 'items': QUESTIONS_TO_ASK[:4]})
    ai = None
    if bool(body.get('use_ai', True)):
        prompt = ('TASK: Explain this single lab result to the patient: what the test is, what the value generally '
                  'means, what may be important, and what to discuss with their physician. Do not diagnose.\n'
                  f"{platform.data_block('result', f'{name}: {r.result_value} {r.result_unit or ''}; reference {order.test.normal_range if order.test else ''}; flagged={flag}', 500)}")
        ai = _ai('patient.explain_result', patient, {'order': order.id, 'value': r.result_value}, prompt)
    return jsonify({'ok': True, 'title': name, 'sections': sections, 'ai': ai, 'footer': SAFETY_FOOTER})


@patient_ai_bp.route('/explain-medication', methods=['POST'])
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
@limiter.limit('10/minute')
def explain_medication_view():
    patient = _patient()
    if patient is None:
        return jsonify({'ok': False, 'error': 'No patient profile.'}), 404
    body = request.get_json(silent=True) or {}
    item = db.session.get(PrescriptionItem, int(body.get('item_id') or 0))
    if item is None or item.prescription is None or item.prescription.patient_id != patient.id:
        return jsonify({'ok': False, 'error': 'Medication not found.'}), 404
    med = item.medication
    name = med.generic_name if med else 'Medication'
    info = explain_medication(name)
    sections = []
    if info:
        sections += [{'title': 'Purpose', 'items': [info['purpose']]},
                     {'title': 'General instructions', 'items': [info['instructions']]},
                     {'title': 'Common precautions', 'items': [info['precautions']]}]
    sections.append({'title': 'Your prescription', 'items': [f"{name} {item.dosage or ''} {item.frequency or ''} {item.duration or ''}".strip()
                                                             + (f" — {item.instructions}" if item.instructions else '')]})
    sections.append({'title': 'Questions to ask your physician', 'items': [
        'What is this medicine for in my case?', 'How long should I take it?',
        'What side effects should I watch for?', 'Does it interact with my other medicines?']})
    ai = None
    if bool(body.get('use_ai', True)):
        prompt = ('TASK: Explain this medicine to the patient: purpose, general instructions, common precautions, '
                  'and questions to ask their physician. Never tell them to change or stop it.\n'
                  f"{platform.data_block('medication', f'{name} {item.dosage or ''} {item.frequency or ''} {item.instructions or ''}', 400)}")
        ai = _ai('patient.explain_medication', patient, {'item': item.id}, prompt)
    return jsonify({'ok': True, 'title': name, 'sections': sections, 'ai': ai, 'footer': SAFETY_FOOTER})


@patient_ai_bp.route('/prepare-appointment', methods=['POST'])
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def prepare_appointment():
    """Static-first: checklist, questions, documents. No AI call by default."""
    patient = _patient()
    if patient is None:
        return jsonify({'ok': False, 'error': 'No patient profile.'}), 404
    body = request.get_json(silent=True) or {}
    appt = _own_or_none(db.session.get(Appointment, int(body.get('appointment_id') or 0)), patient)
    if appt is None:
        return jsonify({'ok': False, 'error': 'Appointment not found.'}), 404
    prep = preparation_for(appt.visit_type, appt.reason)
    sections = [
        {'title': 'Before your visit', 'items': prep},
        {'title': 'Questions you may want to ask', 'items': QUESTIONS_TO_ASK},
        {'title': 'Documents to bring', 'items': ['ID and insurance card', 'Medication list', 'Previous reports or results']},
        {'title': 'Your appointment', 'items': [f"{appt.scheduled_at.strftime('%A %d %B %Y, %H:%M') if appt.scheduled_at else ''} with "
                                               f"Dr. {appt.doctor.user.full_name if appt.doctor and appt.doctor.user else 'your physician'}"
                                               + (f" — reason: {appt.reason}" if appt.reason else '')]},
    ]
    platform.record_usage('patient.prepare_appointment', 'ok', provider='local', patient_id=patient.id)
    return jsonify({'ok': True, 'title': 'Prepare for your appointment', 'sections': sections, 'ai': None,
                    'footer': SAFETY_FOOTER})


@patient_ai_bp.route('/explain-term', methods=['POST'])
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
@limiter.limit('20/minute')
def explain_term_view():
    patient = _patient()
    if patient is None:
        return jsonify({'ok': False, 'error': 'No patient profile.'}), 404
    body = request.get_json(silent=True) or {}
    term = platform.sanitize_text(body.get('term') or '', 80)
    if not term:
        return jsonify({'ok': False, 'error': 'Enter a term.'}), 400
    local = explain_term(term)
    ai = None
    if local is None and bool(body.get('use_ai', True)):
        ai = _ai('patient.explain_term', patient, {'term': term.lower()},
                 f"TASK: Explain this medical term to a patient in two plain sentences. No diagnosis.\n{platform.data_block('term', term, 80)}",
                 max_tokens=120)
    return jsonify({'ok': True, 'term': term, 'explanation': local, 'ai': ai, 'footer': SAFETY_FOOTER})


@patient_ai_bp.route('/summary', methods=['POST'])
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
@limiter.limit('5/minute')
def summary():
    """Summarise the patient's own record (patient-visible data only)."""
    patient = _patient()
    if patient is None:
        return jsonify({'ok': False, 'error': 'No patient profile.'}), 404
    from app.services.ai.clinical_context import build_context
    ctx = build_context(patient, labs=6, imaging=3, encounters=3)
    visible = {
        'problems': [p['description'] for p in ctx['problems'] if p['status'] != 'Resolved'][:6],
        'medications': [f"{m['name']} {m['frequency'] or ''}".strip() for m in ctx['medications']][:8],
        'results': [f"{l['test']}: {l['value']} {l['unit'] or ''}" for l in ctx['labs'] if l['status'] in ('Verified', 'Locked', 'Finalized')][:6],
        'appointments': [f"{a['when']} ({a['status']})" for a in ctx['appointments'] if a['status'] in ('Scheduled', 'Confirmed')][:3],
        'follow_ups': [f"{f['when']}: {f['reason'] or ''}" for f in ctx['follow_ups']][:3],
    }
    sections = [{'title': k.replace('_', ' ').title(), 'items': v or ['None on record']} for k, v in visible.items()]
    terms = find_terms(' '.join(visible['problems'] + visible['results']))
    if terms:
        sections.append({'title': 'Terms explained', 'items': [f'{t}: {x}' for t, x in list(terms.items())[:6]]})
    ai = None
    if bool((request.get_json(silent=True) or {}).get('use_ai', True)):
        prompt = ('TASK: Write a short, reassuring plain-language summary of this patient\'s own record for the patient: '
                  'conditions being managed, medicines, recent results, upcoming visits. No diagnosis, no treatment changes.\n'
                  f"{platform.data_block('record', str(visible), 3000)}")
        ai = _ai('patient.summary', patient, visible, prompt, max_tokens=350)
    return jsonify({'ok': True, 'title': 'My health summary', 'sections': sections, 'ai': ai, 'footer': SAFETY_FOOTER})


@patient_ai_bp.route('/questions', methods=['POST'])
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
@limiter.limit('10/minute')
def questions():
    patient = _patient()
    if patient is None:
        return jsonify({'ok': False, 'error': 'No patient profile.'}), 404
    body = request.get_json(silent=True) or {}
    concern = platform.sanitize_text(body.get('concern') or '', 300)
    sections = [{'title': 'Questions to prepare', 'items': QUESTIONS_TO_ASK}]
    ai = None
    if concern and bool(body.get('use_ai', True)):
        ai = _ai('patient.questions', patient, {'concern': concern},
                 f"TASK: Suggest 6 clear questions the patient could ask their physician about this concern. No diagnosis.\n{platform.data_block('concern', concern, 300)}",
                 max_tokens=250)
    return jsonify({'ok': True, 'title': 'Prepare questions', 'sections': sections, 'ai': ai, 'footer': SAFETY_FOOTER})
