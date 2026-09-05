"""Clinician template form-designer DSL (#19).

`ClinicianTemplate.sections` is a declarative JSON list — the native form
designer. Each item may carry:

    {"key": "hr", "label": "Heart rate", "type": "number", "required": true}
    {"key": "bp", "label": "Blood pressure", "type": "text"}
    {"key": "site", "label": "Pain site", "type": "select",
     "options": ["Head", "Chest", "Abdomen", "Limbs"]}
    {"key": "smoker", "label": "Smoking", "type": "radio", "options": ["Yes", "No"]}
    {"key": "meds", "label": "Current meds", "type": "checkbox",
     "options": ["Aspirin", "Beta blocker", "Statin"]}
    {"key": "s", "label": "Subjective", "type": "textarea"}

Types without a value default to a textarea, so legacy sections keep working.
"""
import json

ALLOWED_TYPES = ('textarea', 'text', 'select', 'number', 'date', 'radio', 'checkbox')


def _split_options(raw):
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(o).strip() for o in raw if str(o).strip()]
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith('['):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(o).strip() for o in parsed if str(o).strip()]
            except (TypeError, ValueError):
                pass
        return [o.strip() for o in raw.replace(',', '|').split('|') if o.strip()]
    return []


def normalize_sections(rows):
    """Validate/clean a section list. Raises ValueError on structural problems."""
    if isinstance(rows, str):
        try:
            rows = json.loads(rows or '[]')
        except (TypeError, ValueError) as exc:
            raise ValueError('Sections are not valid JSON.') from exc
    if not isinstance(rows, list):
        raise ValueError('Sections must be a JSON list.')
    seen = set()
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get('key') or '').strip()
        if not key:
            continue
        ftype = str(row.get('type') or 'textarea').strip().lower()
        if ftype not in ALLOWED_TYPES:
            raise ValueError(f'Unsupported field type "{ftype}" (allowed: '
                             + ', '.join(ALLOWED_TYPES) + ').')
        section = {
            'key': key,
            'label': (str(row.get('label') or '') or key).strip(),
            'placeholder': str(row.get('placeholder') or '').strip(),
            'type': ftype,
            'required': bool(row.get('required')),
        }
        if ftype in ('select', 'radio', 'checkbox'):
            options = _split_options(row.get('options'))
            if not options:
                raise ValueError(f'Section "{key}" of type {ftype} needs options.')
            section['options'] = options
        out.append(section)
    if len(out) != len({s['key'] for s in out}):
        seen = set()
        for s in out:
            if s['key'] in seen:
                raise ValueError(f'Duplicate section key "{s["key"]}".')
            seen.add(s['key'])
    return out


def template_allowed_for(tpl, user):
    """Empty allowed_roles = open to any clinical role; else any-listed-role match."""
    allowed = [r.strip() for r in (tpl.allowed_roles or '').split(',') if r.strip()]
    if not allowed:
        return True
    return user.has_any_role(*allowed)


def format_structured_notes(sections, values):
    """Render completed sections into the clinical note text (label-anchored)."""
    blocks = []
    for s in sections:
        value = values.get(s['key'])
        if isinstance(value, (list, tuple)):
            value = ', '.join(v for v in value if str(v).strip())
        value = (value or '').strip()
        if not value:
            continue
        blocks.append(f"## {s['label']}\n{value}")
    return '\n\n'.join(blocks)