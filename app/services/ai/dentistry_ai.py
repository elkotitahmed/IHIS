"""Dentistry AI: chart summarisation, finding summary, treatment-plan draft,
patient education and abnormality assistance for the odontogram.

Local-first (deterministic reading of the dental chart, procedures, records
and imaging), with optional Gemini drafting through the AI platform. The
dentist remains responsible; nothing is written to the record.
"""
import json

from app.models import (DentalProcedure, DentalRecord, PatientDocument, RadiologyOrder)
from app.services.ai import platform
from app.services.ai.platform import sanitize_text

ABNORMAL_STATES = {'caries', 'decay', 'missing', 'fracture', 'fractured', 'abscess', 'mobile',
                   'mobility', 'impacted', 'root canal', 'rct', 'periodontal', 'crown', 'extraction'}


def _d(v):
    return v.strftime('%Y-%m-%d') if v else None


def dental_context(patient):
    pid = patient.id
    from app.models import DentalChart
    charts = (DentalChart.query.filter_by(patient_id=pid)
              .order_by(DentalChart.created_at.desc()).limit(64).all())
    current = {}
    for c in charts:                       # newest first: first seen wins
        key = (c.tooth_number, getattr(c, 'surface', None))
        if key not in current:
            current[key] = c
    teeth = [{'tooth': c.tooth_number, 'surface': getattr(c, 'surface', None),
              'condition': sanitize_text(getattr(c, 'condition', None) or getattr(c, 'status', None), 60),
              'notes': sanitize_text(getattr(c, 'notes', None), 120), 'date': _d(c.created_at)}
             for c in current.values()]
    abnormal = [t for t in teeth if t['condition'] and any(k in t['condition'].lower() for k in ABNORMAL_STATES)]
    records = [{'date': _d(r.created_at), 'complaint': sanitize_text(getattr(r, 'chief_complaint', None) or getattr(r, 'complaint', None), 200),
                'findings': sanitize_text(getattr(r, 'examination_findings', None), 400),
                'diagnosis': sanitize_text(getattr(r, 'diagnosis', None), 200),
                'plan': sanitize_text(getattr(r, 'treatment_plan', None), 400),
                'perio': sanitize_text(getattr(r, 'periodontal_notes', None), 200)}
               for r in DentalRecord.query.filter_by(patient_id=pid).order_by(DentalRecord.created_at.desc()).limit(4).all()]
    procedures = [{'date': _d(p.performed_at or p.scheduled_at), 'name': sanitize_text(p.procedure_name, 120),
                   'tooth': p.tooth_number, 'status': p.status, 'notes': sanitize_text(p.notes, 120)}
                  for p in DentalProcedure.query.filter_by(patient_id=pid).order_by(DentalProcedure.created_at.desc()).limit(10).all()]
    imaging = [{'date': _d(o.order_date), 'study': o.imaging_type.name if o.imaging_type else 'Imaging',
                'impression': sanitize_text(o.report.impression, 300) if o.report else None}
               for o in RadiologyOrder.query.filter_by(patient_id=pid).order_by(RadiologyOrder.order_date.desc()).limit(10).all()
               if o.imaging_type and any(k in o.imaging_type.name.lower() for k in ('dental', 'panor', 'opg', 'periap', 'bitewing', 'cbct', 'tooth', 'jaw'))]
    images = [{'id': d.id, 'title': sanitize_text(d.title, 80), 'date': _d(d.uploaded_at)}
              for d in PatientDocument.query.filter_by(patient_id=pid).order_by(PatientDocument.uploaded_at.desc()).limit(20).all()
              if (d.file_url or '').lower().endswith(('.png', '.jpg', '.jpeg')) and 'dent' in ((d.category or '') + (d.title or '') + (d.document_type or '')).lower()]
    return {'teeth': teeth, 'abnormal_teeth': abnormal, 'records': records, 'procedures': procedures,
            'imaging': imaging, 'images': images,
            'allergies': [sanitize_text(a.substance, 80) for a in patient.allergy_list] if hasattr(patient, 'allergy_list') else []}


def dental_summary(patient):
    ctx = dental_context(patient)
    pending = [p for p in ctx['procedures'] if p['status'] in ('Planned', 'Scheduled', 'InProgress')]
    return {
        'context': ctx,
        'sections': [
            {'title': 'Chart summary', 'items': [f"{len(ctx['teeth'])} charted tooth entries, {len(ctx['abnormal_teeth'])} with abnormal states"]},
            {'title': 'Abnormal teeth (from chart)', 'items': [f"Tooth {t['tooth']}{' ' + t['surface'] if t['surface'] else ''}: {t['condition']}" for t in ctx['abnormal_teeth']] or ['None charted']},
            {'title': 'Findings (latest record)', 'items': [x for r in ctx['records'][:1] for x in (r['complaint'], r['findings'], r['perio']) if x] or ['No dental record']},
            {'title': 'Treatment plan on file', 'items': [r['plan'] for r in ctx['records'][:1] if r['plan']] or ['No plan recorded']},
            {'title': 'Procedures', 'items': [f"{p['date']} {p['name']}{' tooth ' + str(p['tooth']) if p['tooth'] else ''} ({p['status']})" for p in ctx['procedures'][:8]] or ['None']},
            {'title': 'Pending procedures', 'items': [f"{p['name']} ({p['status']})" for p in pending] or ['None']},
            {'title': 'Dental imaging', 'items': [f"{im['date']} {im['study']}: {im['impression'] or 'no report'}" for im in ctx['imaging']] or ['None']},
        ],
    }


_TASKS = {
    'summary': 'Summarise the dental chart and history for the dentist in max 8 bullets (abnormal teeth, active problems, pending work, risks).',
    'findings': 'From the charted states, records and imaging, list the findings that need attention and the ones to monitor (max 8 bullets). Do not diagnose; phrase as "consider".',
    'plan': 'Draft a phased treatment plan (Phase 1 urgent/pain, Phase 2 disease control, Phase 3 restorative, Phase 4 maintenance) from the charted abnormalities and pending procedures. Max 10 lines. The dentist edits and confirms.',
    'education': 'Write patient-friendly education for this dental situation: what was found, why treatment matters, home care, warning signs, when to contact the clinic. Max 8 short bullets, no jargon.',
    'abnormality': 'For each abnormal tooth entry, suggest what the dentist may want to examine or image next and possible explanations to consider. Max 8 bullets.',
}


def run_dental_action(action, patient, use_ai=True):
    action = action if action in _TASKS else 'summary'
    summary = dental_summary(patient)
    out = {'action': action, 'verified': summary['sections'], 'ai': None,
           'disclaimer': 'AI-assisted dental analysis for a licensed dentist. Not a diagnosis; the dentist reviews and confirms.'}
    if not use_ai:
        return out
    ctx = summary['context']
    prompt = (f"TASK: {_TASKS[action]}\nPlain text bullets, no HTML.\n"
              f"{platform.data_block('dental_chart', json.dumps(ctx, ensure_ascii=False, default=str), 10000)}")
    res = platform.run_ai(f'dentistry.{action}', patient.id, {'ctx': ctx, 'action': action}, prompt,
                          heavy=action in ('plan',), max_tokens=600)
    out['ai'] = {k: res.get(k) for k in ('status', 'text', 'message', 'usage_id', 'cached')}
    return out
