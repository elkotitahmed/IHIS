"""Which of the four local imaging models a user may see and open.

One policy, applied everywhere the models appear: the sidebar AI TOOLS group,
the dashboard AI block, the AI Hub catalogue and the model routes themselves.

* Roles without a specialty get a fixed set (radiologists: chest, fracture,
  tooth; nurses: fracture, tooth, skin; physiotherapists: fracture; dentists:
  tooth, fracture, skin; admins: everything).
* Physicians get the models that belong to their specialty's workflow: a
  dermatologist sees only Skin Lesion Detection, an orthopaedic surgeon only
  Fracture Detection, an internist only Chest X-ray Screening, and so on.
  A physician without a specialty on file (or with a specialty that is not in
  the table) keeps the full physician set.
"""
MODEL_KEYS = ('chest', 'fracture', 'tooth', 'skin')
ENDPOINT_BY_KEY = {
    'chest': '/ai/chest-xray',
    'fracture': '/ai/fracture-detection',
    'tooth': '/ai/tooth-segmentation',
    'skin': '/ai/skin-lesion-detection',
}
KEY_BY_ENDPOINT = {v: k for k, v in ENDPOINT_BY_KEY.items()}
# The AI Hub files the skin model under the 'dermatology' key.
HUB_KEY_ALIASES = {'dermatology': 'skin'}

ROLE_MODELS = {
    'SuperAdmin': set(MODEL_KEYS), 'Admin': set(MODEL_KEYS),
    'Doctor': {'chest', 'fracture', 'skin'},          # narrowed by specialty below
    'Radiologist': {'chest', 'fracture', 'tooth'},
    'RadiologyTechnician': {'chest'},
    'Nurse': {'fracture', 'tooth', 'skin'},
    'Physiotherapist': {'fracture'},
    'Dentist': {'tooth', 'fracture', 'skin'},   # jaw fractures, oral/facial lesions (= route decorators)
}

# Physician specialty -> models used in that specialty's workflow.
SPECIALTY_MODELS = {
    'Dermatology': {'skin'},
    'Orthopedics': {'fracture'},
    'Pulmonology': {'chest'},
    'Cardiology': {'chest'},
    'Internal Medicine': {'chest'},
    'Nephrology': {'chest'},
    'Endocrinology': {'chest'},
    'Gastroenterology': {'chest'},
    'Oncology': {'chest', 'skin'},
    'Emergency Medicine': {'chest', 'fracture'},
    'Surgery': {'chest', 'fracture'},
    'Pediatrics': {'chest', 'fracture'},
    'Family Medicine': {'chest', 'fracture', 'skin'},
    'Neurology': set(), 'Psychiatry': set(), 'Ophthalmology': set(), 'ENT': set(),
    'Gynecology': set(), 'Urology': set(),
}


def physician_specialty(user):
    """Specialty name of the user's physician profile, or None."""
    try:
        from app.models import Doctor
        doc = Doctor.query.filter_by(user_id=user.id).first()
        return doc.specialty.name if doc and doc.specialty else None
    except Exception:  # noqa: BLE001 - never break a page over this lookup
        return None


def allowed_models(user, roles=None):
    """Set of model keys the user may see/open, given the effective roles."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    roles = set(roles) if roles is not None else {r.name for r in user.roles}
    allowed = set()
    for role in roles:
        if role == 'Doctor':
            base = set(ROLE_MODELS['Doctor'])
            spec = physician_specialty(user)
            if spec in SPECIALTY_MODELS:
                base &= SPECIALTY_MODELS[spec]
            allowed |= base
        else:
            allowed |= ROLE_MODELS.get(role, set())
    return allowed


def model_allowed(user, key, roles=None):
    return key in allowed_models(user, roles)


# Companion engines that only make sense next to their imaging models.
RELATED_ENDPOINTS = {
    '/ai/copilot/radiology': {'chest', 'fracture'},   # report critical-finding engine
    '/ai/copilot/dentistry': {'tooth'},               # dental chart analysis
}


def hub_item_allowed(item, allowed):
    """True when a hub catalogue item is not one of the four models (or their
    companion engines), or is an allowed one (recognised by URL)."""
    path = (item.get('url') or '').split('?')[0]
    key = KEY_BY_ENDPOINT.get(path)
    if key is not None:
        return key in allowed
    needs = RELATED_ENDPOINTS.get(path)
    return needs is None or bool(needs & allowed)


def effective_roles_for(user):
    """Roles used for navigation (honours the SuperAdmin role preview)."""
    try:
        from flask import session
        preview = session.get('preview_role')
        if preview and user.has_role('SuperAdmin'):
            return [preview]
    except Exception:  # noqa: BLE001
        pass
    return [r.name for r in user.roles]


def require_model(key):
    """Route guard: redirect to the AI Hub when the model is outside the user's
    role/specialty workflow. Returns a response to return, or None."""
    from flask import flash, redirect, url_for
    from flask_login import current_user
    if model_allowed(current_user, key, effective_roles_for(current_user)):
        return None
    flash('This AI model is not part of your specialty workflow. Ask an administrator if you need it.', 'warning')
    return redirect(url_for('ai.ai_hub'))
