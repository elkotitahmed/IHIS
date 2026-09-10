"""AI Hub catalogue: every AI capability in iHIS, role-filtered, with an
honest status.

Status is computed from what actually exists on this installation:

* ``AVAILABLE``   – the capability works right now (rules, a loaded model,
                    or a configured AI provider).
* ``LIMITED``     – the deterministic part works; the optional AI narrative
                    is missing (no provider key, budget reached, cooldown).
* ``COMING SOON`` – the model weights are not installed on this server.

Nothing here calls the AI provider; it only inspects configuration, model
files and the platform budget, so it is cheap enough for a page load.
"""
from flask import url_for

from app.services.ai import platform
from app.services.ai.copilot import GROUPS, catalogue as copilot_catalogue, predictive_catalogue

CLINICAL = {'Doctor', 'Nurse', 'Dentist', 'Physiotherapist', 'Pharmacist',
            'Radiologist', 'RadiologyTechnician', 'Admin', 'SuperAdmin'}
ADMIN = {'Admin', 'SuperAdmin'}


def _safe(fn):
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001 - a missing optional dependency is "not available"
        return False


def _gemini_status(state):
    if state == 'READY':
        return 'AVAILABLE', ''
    if state in ('LIMITED', 'LIMIT_REACHED'):
        return 'LIMITED', 'Local tools work; the AI narrative is paused.'
    return 'LIMITED', 'AI provider disabled; local tools work.'


def build(role_names, has_permission=None):
    """Return ``{'status', 'groups': [...]}`` for the given roles.

    ``has_permission(name)`` (optional) hides links whose destination is
    permission-gated (inbox, alerts, drug check).
    """
    roles = set(role_names or ())
    perm = has_permission or (lambda name: True)
    st = platform.status()
    gem_status, gem_note = _gemini_status(st['state'])

    from app.services.ai.chest_xray import chest_model_available
    from app.services.ai.dictation import dictation_available
    from app.services.ai.fracture_detection import fracture_model_available
    from app.services.ai.skin_lesion_classification import skin_model_available
    from app.services.ai.tooth_segmentation import tooth_model_available

    fracture = _safe(fracture_model_available)
    chest = _safe(chest_model_available)
    dictation = _safe(dictation_available)
    skin = _safe(skin_model_available)
    tooth = _safe(tooth_model_available)
    predictive = {p['key']: p for p in predictive_catalogue(roles)}

    groups = []

    # 1. AI Clinical Copilot — one entry point, contextual actions ----------
    cop = copilot_catalogue(roles)
    if cop and roles & CLINICAL and perm('AI_USE'):
        items = []
        icons = {k: i for k, _, _, i in GROUPS}
        for grp in cop:
            if grp['key'] == 'PREDICTIVE':
                continue
            n = len(grp['actions'])
            items.append({
                'key': f"copilot.{grp['key'].lower()}", 'label': grp['label'], 'label_ar': grp['label_ar'],
                'desc': f'{n} action(s) on the patient in context: verified local data first, AI narrative on request.',
                'desc_ar': f'{n} إجراء على المريض الحالي: بيانات محلية موثّقة أولًا، ثم سرد الذكاء عند الطلب.',
                'icon': icons.get(grp['key'], 'fa-wand-magic-sparkles'), 'engine': 'hybrid',
                'status': gem_status, 'note': gem_note, 'copilot': True, 'url': None,
            })
        groups.append({
            'key': 'copilot', 'label': 'AI Clinical Copilot', 'label_ar': 'المساعد السريري الذكي',
            'desc': 'One assistant on every clinical page. It reads the chart, never writes to it.',
            'desc_ar': 'مساعد واحد في كل صفحة سريرية. يقرأ الملف ولا يكتب فيه أبدًا.',
            'items': items,
        })

    # 2. Predictive & imaging AI ----------------------------------------------
    pred_items = []
    derm = predictive.get('dermatology')
    if derm and derm.get('allowed', True):
        pred_items.append({
            'key': 'dermatology', 'label': 'Dermatology AI', 'label_ar': 'ذكاء الجلدية',
            'desc': 'Skin Lesion Detection: lesion classification with image-quality checks and an ABCDE checklist.',
            'desc_ar': 'كشف آفات الجلد: تصنيف الآفة مع فحص جودة الصورة وقائمة ABCDE.',
            'icon': 'fa-person-circle-question', 'engine': 'model',
            'status': 'AVAILABLE' if skin else 'COMING SOON',
            'note': '' if skin else 'Model weights are not installed on this server.',
            'url': url_for('ai.skin_lesion_detection'),
        })
    rad = predictive.get('radiology')
    if rad and rad.get('allowed', True):
        pred_items.append({
            'key': 'radiology', 'label': 'Radiology AI', 'label_ar': 'ذكاء الأشعة',
            'desc': 'Critical-finding engine on every signed report: rules + local classifier → alert → urgent task → acknowledgement → audit.',
            'desc_ar': 'محرك النتائج الحرجة على كل تقرير: قواعد + مصنّف محلي ← تنبيه ← مهمة عاجلة ← إقرار ← تدقيق.',
            'icon': 'fa-x-ray', 'engine': 'rules',
            'status': rad['status'] if rad['status'] == 'AVAILABLE' else 'LIMITED',
            'note': '' if rad['status'] == 'AVAILABLE' else 'Rules run; the local classifier package is missing.',
            'url': url_for('copilot.radiology_ai'),
        })
    if roles & ({'Radiologist', 'RadiologyTechnician', 'Doctor'} | ADMIN):
        pred_items.append({
            'key': 'chest', 'label': 'Chest X-ray Screening', 'label_ar': 'فحص أشعة الصدر',
            'desc': '18 findings on a frontal chest X-ray (pneumothorax, effusion, pneumonia, cardiomegaly, nodule…); confident critical findings raise an alert.',
            'desc_ar': '18 نتيجة في أشعة الصدر الأمامية (استرواح صدري، انصباب، التهاب رئوي، تضخم قلب، عقيدة…)؛ النتائج الحرجة الواثقة تُنشئ تنبيهًا.',
            'icon': 'fa-lungs', 'engine': 'model',
            'status': 'AVAILABLE' if chest else 'COMING SOON',
            'note': '' if chest else 'torchxrayvision is not installed on this server.',
            'url': url_for('ai.chest_xray'),
        })
    if roles & ({'Radiologist', 'Doctor', 'Nurse', 'Physiotherapist', 'Dentist'} | ADMIN):
        pred_items.append({
            'key': 'fracture', 'label': 'Fracture Detection', 'label_ar': 'كشف الكسور',
            'desc': 'Bone X-ray fracture detection with an annotated image for the radiologist to confirm.',
            'desc_ar': 'كشف الكسور في أشعة العظام مع صورة موضّحة يؤكدها أخصائي الأشعة.',
            'icon': 'fa-bone', 'engine': 'model',
            'status': 'AVAILABLE' if fracture else 'COMING SOON',
            'note': '' if fracture else 'Model weights are not installed on this server.',
            'url': url_for('ai.fracture_detection'),
        })
    dent = predictive.get('dentistry')
    if dent and dent.get('allowed', True):
        pred_items.append({
            'key': 'dentistry', 'label': 'Dentistry AI', 'label_ar': 'ذكاء الأسنان',
            'desc': 'Dental chart analysis (local rules) with AI explanation; tooth segmentation when the model is installed.',
            'desc_ar': 'تحليل مخطط الأسنان (قواعد محلية) مع شرح الذكاء؛ وتجزئة الأسنان عند توفر النموذج.',
            'icon': 'fa-tooth', 'engine': 'hybrid', 'status': 'AVAILABLE', 'note': '',
            'url': url_for('copilot.dentistry_ai'),
        })
    if roles & ({'Dentist', 'Radiologist', 'Nurse'} | ADMIN):
        pred_items.append({
            'key': 'tooth', 'label': 'Tooth Segmentation', 'label_ar': 'تجزئة الأسنان',
            'desc': 'Panoramic X-ray tooth segmentation mask for dental planning.',
            'desc_ar': 'قناع تجزئة الأسنان من الأشعة البانورامية لتخطيط العلاج.',
            'icon': 'fa-teeth', 'engine': 'model',
            'status': 'AVAILABLE' if tooth else 'COMING SOON',
            'note': '' if tooth else 'Model weights are not installed on this server.',
            'url': url_for('ai.tooth_segmentation'),
        })
    # Specialty policy: a dermatologist sees only the skin model, an orthopaedic
    # surgeon only fracture detection, etc. (app.services.ai.specialty_models).
    try:
        from flask_login import current_user as _cu
        from app.services.ai import specialty_models as _sm
        if _cu.is_authenticated:
            _allowed = _sm.allowed_models(_cu, roles)
            pred_items = [it for it in pred_items if _sm.hub_item_allowed(it, _allowed)]
    except Exception:  # noqa: BLE001 - outside a request the catalogue is unfiltered
        pass
    if pred_items:
        groups.append({
            'key': 'predictive', 'label': 'Predictive & imaging AI', 'label_ar': 'الذكاء التنبؤي وتحليل الصور',
            'desc': 'Local models and rule engines. A clinician confirms every finding.',
            'desc_ar': 'نماذج محلية ومحركات قواعد. يؤكد الطبيب كل نتيجة.',
            'items': pred_items,
        })

    # 3. Safety engines — rule-first, always on ------------------------------
    safety = []
    if roles & CLINICAL and perm('ALERT_VIEW'):
        safety.append({
            'key': 'alerts', 'label': 'Critical-finding alerts', 'label_ar': 'تنبيهات النتائج الحرجة',
            'desc': 'Open, acknowledge, act and resolve. Escalates automatically when nobody responds in time.',
            'desc_ar': 'فتح، إقرار، إجراء، حل. يُصعَّد تلقائيًا عند عدم الاستجابة في الوقت المحدد.',
            'icon': 'fa-triangle-exclamation', 'engine': 'rules', 'status': 'AVAILABLE', 'note': '',
            'url': url_for('clinical.alerts_all'),
        })
    if roles & ({'Doctor', 'Nurse'} | ADMIN):
        safety.append({
            'key': 'alert_engine', 'label': 'Clinical alert engine', 'label_ar': 'محرك التنبيهات السريرية',
            'desc': 'Rule scan of vitals, labs and medications for your patients. Nothing is auto-finalised.',
            'desc_ar': 'فحص قواعدي للعلامات الحيوية والمختبر والأدوية لمرضاك. لا يُنهى شيء تلقائيًا.',
            'icon': 'fa-bell', 'engine': 'rules', 'status': 'AVAILABLE', 'note': '',
            'url': url_for('ai.clinical_alerts'),
        })
    if roles & ({'Doctor', 'Nurse', 'Pharmacist', 'Dentist'} | ADMIN):
        safety.append({
            'key': 'med_safety', 'label': 'Medication safety', 'label_ar': 'سلامة الأدوية',
            'desc': 'Allergy, cross-reactivity and interaction checks as you prescribe (local formulary + 160k DDInter reference pairs); rules are authoritative.',
            'desc_ar': 'فحص الحساسية والتفاعل المتصالب والتداخلات أثناء الوصف؛ القواعد هي المرجع.',
            'icon': 'fa-shield-halved', 'engine': 'rules', 'status': 'AVAILABLE', 'note': '',
            'url': url_for('pharmacy.drug_check') if (roles & ({'Pharmacist', 'Doctor', 'Nurse'} | ADMIN)) and perm('DRUG_INTERACTION') else None,
            'copilot': True,
        })
    if roles & ({'Doctor', 'Nurse'} | ADMIN):
        safety.append({
            'key': 'scores', 'label': 'Early-warning scores', 'label_ar': 'مؤشرات الإنذار المبكر',
            'desc': 'NEWS2 on every vital-sign entry, qSOFA sepsis screen and LACE readmission risk at discharge. Standard, deterministic, explainable.',
            'desc_ar': 'NEWS2 عند كل قياس للعلامات الحيوية، qSOFA لفحص الإنتان، وLACE لخطر إعادة الدخول عند الخروج. معيارية وحتمية وقابلة للتفسير.',
            'icon': 'fa-heart-pulse', 'engine': 'rules', 'status': 'AVAILABLE', 'note': '',
            'url': url_for('nursing.dashboard') if 'Nurse' in roles else (url_for('clinical.alerts_all') if perm('ALERT_VIEW') else None),
        })
    if roles & CLINICAL and perm('INBOX_VIEW'):
        safety.append({
            'key': 'inbox', 'label': 'Smart inbox', 'label_ar': 'صندوق الوارد الذكي',
            'desc': '"What needs my attention first?" — deterministic ranking of results, alerts and tasks; AI only narrates.',
            'desc_ar': '"ما الذي يحتاج انتباهي أولًا؟" ترتيب قاعدي للنتائج والتنبيهات والمهام؛ الذكاء يسرد فقط.',
            'icon': 'fa-inbox', 'engine': 'hybrid', 'status': gem_status, 'note': gem_note,
            'url': url_for('clinical.inbox'),
        })
    if safety:
        groups.append({
            'key': 'safety', 'label': 'Safety engines', 'label_ar': 'محركات السلامة',
            'desc': 'Always on, deterministic and auditable. AI adds explanation, never authority.',
            'desc_ar': 'تعمل دائمًا، حتمية وقابلة للتدقيق. الذكاء يضيف الشرح لا السلطة.',
            'items': safety,
        })

    # 4. Documentation & coding ----------------------------------------------
    docs = []
    if roles & ({'Doctor', 'Dentist', 'Nurse'} | ADMIN):
        docs.append({
            'key': 'dictation', 'label': 'Clinical dictation', 'label_ar': 'الإملاء السريري',
            'desc': 'Microphone button on every note field: local speech-to-text (faster-whisper, ~75 MB); audio never leaves the server. Then "Structure note (SOAP)" in the Copilot.',
            'desc_ar': 'زر ميكروفون على كل حقل ملاحظات: تحويل كلام لنص محليًا (faster-whisper، ~75 ميجا)؛ الصوت لا يغادر الخادم. ثم "هيكلة الملاحظة" في المساعد.',
            'icon': 'fa-microphone', 'engine': 'model',
            'status': 'AVAILABLE' if dictation else 'COMING SOON',
            'note': '' if dictation else 'faster-whisper is not installed on this server.',
            'url': None, 'inline': 'Inside note fields',
        })
        docs.append({
            'key': 'autocomplete', 'label': 'Smart autocomplete', 'label_ar': 'الإكمال الذكي',
            'desc': 'Clinical phrases as you type in notes (local first, AI when ready). Tab accepts, Esc dismisses.',
            'desc_ar': 'عبارات سريرية أثناء الكتابة (محلي أولًا ثم الذكاء). Tab للقبول وEsc للتجاهل.',
            'icon': 'fa-keyboard', 'engine': 'hybrid', 'status': gem_status, 'note': gem_note,
            'url': None, 'inline': 'Inside EMR notes',
        })
        docs.append({
            'key': 'dx_lookup', 'label': 'ICD-10 diagnosis lookup', 'label_ar': 'بحث تشخيص ICD-10',
            'desc': 'Official ICD-10-CM FY2026 (74,719 codes): type "hyper…" and get Hypertension (I10) first; recents and favourites included.',
            'desc_ar': 'اكتب "hyper…" لتحصل على Hypertension (I10) أولًا، مع الأخيرة والمفضلة.',
            'icon': 'fa-code-medical', 'engine': 'rules', 'status': 'AVAILABLE', 'note': '',
            'url': None, 'inline': 'Inside the diagnosis field',
        })
    if docs:
        groups.append({
            'key': 'documentation', 'label': 'Documentation & coding', 'label_ar': 'التوثيق والترميز',
            'desc': 'Less typing, cleaner notes. Drafts are always reviewed before they touch the chart.',
            'desc_ar': 'كتابة أقل وملاحظات أوضح. تُراجع المسودات دائمًا قبل أن تلمس الملف.',
            'items': docs,
        })

    # 5. Pharmacy ------------------------------------------------------------
    pharm = []
    if roles & ({'Pharmacist'} | ADMIN):
        pharm.append({
            'key': 'drug_reference', 'label': 'Official drug reference', 'label_ar': 'المرجع الدوائي الرسمي',
            'desc': 'FDA-approved labelling (openFDA), RxNorm identifiers and documented interaction partners for every catalogue medication.',
            'desc_ar': 'النشرة المعتمدة (openFDA) ومعرّفات RxNorm والتداخلات الموثقة لكل دواء في الدليل.',
            'icon': 'fa-book-medical', 'engine': 'rules', 'status': 'AVAILABLE', 'note': '',
            'url': url_for('pharmacy.medications'),
        })
    if roles & ({'Pharmacist'} | ADMIN) and perm('MEDICATION_REVIEW'):
        pharm.append({
            'key': 'pharmacist_ai', 'label': 'Clinical Pharmacist AI', 'label_ar': 'الصيدلاني السريري الذكي',
            'desc': 'Medication review workbench for pending prescriptions: interactions, duplicates, dose checks.',
            'desc_ar': 'منصة مراجعة الأدوية للوصفات المعلقة: التداخلات والتكرار وفحص الجرعات.',
            'icon': 'fa-user-doctor', 'engine': 'hybrid', 'status': gem_status, 'note': gem_note,
            'url': url_for('pharmacy.ai_workbench'),
        })
    if pharm:
        groups.append({
            'key': 'pharmacy', 'label': 'Pharmacy AI', 'label_ar': 'ذكاء الصيدلة',
            'desc': 'Rule-based screening first; AI summarises what to verify.',
            'desc_ar': 'فحص قاعدي أولًا؛ الذكاء يلخّص ما يجب التحقق منه.',
            'items': pharm,
        })

    # 6. Operations & governance ---------------------------------------------
    ops = []
    if roles & ADMIN:
        ops.append({
            'key': 'noshow', 'label': 'No-show prediction', 'label_ar': 'توقع الغياب',
            'desc': 'Logistic model trained on this hospital\'s own appointment history; refuses to predict below 150 completed visits.',
            'desc_ar': 'نموذج لوجستي مدرَّب على سجل مواعيد المستشفى نفسه؛ يرفض التوقع تحت 150 زيارة منتهية.',
            'icon': 'fa-user-slash', 'engine': 'model', 'status': 'AVAILABLE', 'note': '',
            'url': url_for('admin.capacity'),
        })
    if 'SuperAdmin' in roles:
        ops.append({
            'key': 'control', 'label': 'AI Control Center', 'label_ar': 'مركز التحكم بالذكاء',
            'desc': 'Budget, cache, failures, audit and unresolved critical alerts. No patient prompts are stored.',
            'desc_ar': 'الميزانية والذاكرة المؤقتة والإخفاقات والتدقيق والتنبيهات الحرجة غير المحلولة. لا تُخزَّن نصوص المرضى.',
            'icon': 'fa-sliders', 'engine': 'rules', 'status': 'AVAILABLE', 'note': '',
            'url': url_for('super_admin.ai_control'),
        })
    if ops:
        groups.append({
            'key': 'operations', 'label': 'Operations & governance', 'label_ar': 'العمليات والحوكمة',
            'desc': 'Capacity, forecasting and the switches that keep AI within budget.',
            'desc_ar': 'السعة والتوقع والمفاتيح التي تُبقي الذكاء ضمن الميزانية.',
            'items': ops,
        })

    # 7. Patient AI ----------------------------------------------------------
    pat = []
    if 'Patient' in roles:
        pat.append({
            'key': 'patient_assistant', 'label': 'My health assistant', 'label_ar': 'مساعدي الصحي',
            'desc': 'Explains your results, medicines and appointments in plain language. Never diagnoses.',
            'desc_ar': 'يشرح نتائجك وأدويتك ومواعيدك بلغة بسيطة. لا يشخّص أبدًا.',
            'icon': 'fa-comment-medical', 'engine': 'hybrid', 'status': gem_status, 'note': gem_note,
            'url': url_for('patient.health_summary'),
        })
    if roles & ({'Patient'} | ADMIN):
        pat.append({
            'key': 'health_insights', 'label': 'Health insights', 'label_ar': 'الرؤى الصحية',
            'desc': 'Risk overview built from the record with a plain-language summary.',
            'desc_ar': 'نظرة على المخاطر مبنية من السجل مع ملخص بلغة بسيطة.',
            'icon': 'fa-brain', 'engine': 'rules', 'status': 'AVAILABLE', 'note': '',
            'url': url_for('ai.health_insights'),
        })
    if pat:
        groups.append({
            'key': 'patient', 'label': 'Patient AI', 'label_ar': 'ذكاء المريض',
            'desc': 'Own record only. Plain language, no diagnosis, no treatment changes.',
            'desc_ar': 'السجل الخاص فقط. لغة بسيطة، بلا تشخيص وبلا تغيير للعلاج.',
            'items': pat,
        })

    total = sum(len(gr['items']) for gr in groups)
    available = sum(1 for gr in groups for it in gr['items'] if it['status'] == 'AVAILABLE')
    return {'status': st, 'groups': groups, 'total': total, 'available': available,
            'models_installed': sum([fracture, skin, tooth])}
