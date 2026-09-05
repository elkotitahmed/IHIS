"""AI layer routes: expose clinical decision-support assistants."""
import os
import re
import time

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, current_app, abort, send_file)
from flask_login import login_required, current_user

from app import db

from app.models import (
    Patient, Doctor, PhysicalTherapist, LabOrder, RadiologyOrder,
    Prescription, Appointment, PatientDocument,
)
from app.routes.decorators import roles_required, log_activity
from app.access import require_patient_access, has_need_to_know
from app.services import alerts as alert_svc
from app.services.patient_safety import patient_safety_context
from app.services.ai import (
    get_assistant, AIClinicalAssistant, AIDiagnosisSupport,
    AIPrescriptionChecker, AIDrugInteractionEngine, AILaboratoryInterpretation,
    AIRadiologyAssistant, AIPatientRiskPrediction, AIRehabilitationAssistant,
    AIHospitalAnalytics, AIClinicalPharmacist,
    AIClinicalNotes, AISmartOrders, AIPatientCommunication,
    AIClinicalAlertEngine, gemini_available,
)
from app.services.ai.ai_medical_coding import AIMedicalCodingAssistant
from app.utils import utcnow

ai_bp = Blueprint('ai', __name__)

CLINICAL = ('Doctor', 'Admin', 'SuperAdmin', 'Nurse', 'Physiotherapist',
            'LabTechnician', 'Radiologist', 'Pharmacist')

IMAGE_EXTS = ('.png', '.jpg', '.jpeg')


def _patient_document_path(doc):
    """Resolve the on-disk path for a PatientDocument from its file_url."""
    rel = (doc.file_url or '').lstrip('/')
    if rel.startswith('static/uploads/'):
        rel = rel[len('static/uploads/'):]
        return os.path.join(current_app.static_folder, 'uploads', rel)
    base = current_app.config.get('UPLOAD_FOLDER') or 'var/uploads'
    return os.path.normpath(os.path.join(base, rel))


def _load_doc(doc_id):
    """Load a patient document a clinician may legitimately analyze."""
    if not doc_id:
        return None
    try:
        doc_id = int(str(doc_id).strip())
        doc = db.session.get(PatientDocument, doc_id)
    except (TypeError, ValueError):
        return None
    if doc is None or doc.patient is None:
        return None
    if not has_need_to_know(doc.patient):
        return None
    return doc


def _resolve_image_input():
    """Return (storage, patient, doc, error) from a form upload or a selected
    patient document (``doc`` field). ``storage`` is a werkzeug FileStorage for
    the analysis; callers must close its stream when done."""
    from werkzeug.datastructures import FileStorage

    doc_id = (request.form.get('doc') or request.args.get('doc') or '').strip()
    if doc_id:
        doc = _load_doc(doc_id)
        if doc is None:
            return None, None, None, 'The selected document is not available to you.'
        path = _patient_document_path(doc)
        if not os.path.isfile(path):
            return None, None, None, 'The selected document is missing on disk.'
        name = os.path.basename(path).lower()
        if not name.endswith(IMAGE_EXTS):
            return None, None, None, 'This document is not a supported image (.png/.jpg/.jpeg).'
        stream = open(path, 'rb')
        return FileStorage(stream=stream, filename=os.path.basename(path)), \
            doc.patient, doc, None

    f = request.files.get('file')
    if f is None or not f.filename:
        return None, None, None, 'Please select an image to analyze.'
    return f, None, None, None


def _alert_on_positive_finding(result, patient, doc=None):
    """Raise an OPEN clinical alert via the shared alert engine when an AI
    image tool returns a positive finding on a patient-linked image.

    Anonymous uploads (no patient context) cannot be tied to a chart, so no
    alert is created. Re-analysing the same document refreshes the alert
    (and can escalate its severity) rather than duplicating it.
    """
    if patient is None or not result or 'error' in result:
        return
    source_id = doc.id if doc is not None else None
    if result.get('feature') == 'fracture' and result.get('detected'):
        counts = result.get('count_by_class') or {}
        summary = ', '.join(f'{k}: {v}' for k, v in counts.items()) or \
            f'{len(result.get("detections") or [])} detection(s)'
        alert_svc.ensure_open_alert(
            patient.id, 'AI_FRACTURE_DETECTED',
            title='Positive AI finding: fracture',
            message=(f'Fracture detection flagged a likely fracture '
                     f'({summary}, ~{result.get("avg_confidence", 0)}% confidence). '
                     f'Clinical review of the imaging is required.'),
            severity='HIGH', source_type='ai_tool', source_id=source_id)
        db.session.commit()
    elif result.get('feature') == 'skin' and result.get('is_melanoma'):
        alert_svc.ensure_open_alert(
            patient.id, 'AI_MELANOMA_SUSPECTED',
            title='Positive AI finding: suspected melanoma',
            message=(f'Skin lesion analysis classified the image as '
                     f'{result.get("prediction", "melanoma")} at '
                     f'~{result.get("percent", 0)}% confidence. '
                     f'Urgent dermatology review is recommended.'),
            severity='HIGH', source_type='ai_tool', source_id=source_id)
        db.session.commit()


def _close_storage(storage):
    try:
        if storage is not None and getattr(storage, 'stream', None) is not None:
            storage.stream.close()
    except Exception:  # noqa: BLE001
        pass


def _patient_or_404(patient_id):
    patient = Patient.query.filter_by(id=patient_id).first()
    if not patient:
        flash('Patient not found.', 'warning')
        return None
    return patient


@ai_bp.route('/health-insights')
@login_required
@roles_required('Patient', 'Admin', 'SuperAdmin')
def health_insights():
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    is_admin = current_user.has_any_role('Admin', 'SuperAdmin')
    if not patient and not is_admin:
        flash('Please complete your patient profile first.', 'warning')
        return redirect(url_for('patient.profile'))
    # Admin/SuperAdmin: allow picking any patient to analyze.
    if is_admin and not patient:
        pids = [p.id for p in Patient.query.all()]
        if not pids:
            flash('No patient records available to analyze.', 'warning')
            return redirect(url_for('admin.dashboard'))
        pid = request.args.get('pid', type=int)
        if pid not in pids:
            pid = pids[0]
        patient = Patient.query.get(pid)
        picker = True
        patients = Patient.query.all()
    else:
        picker = False
        patients = []
    clinical = AIClinicalAssistant()
    risk = AIPatientRiskPrediction()
    summary = clinical.summarize_medical_history(patient.id)
    analysis = clinical.analyze_patient(patient.id)
    risk_report = risk.predict_risk(patient.id)
    return render_template(
        'ai/health_insights.html', title='AI Health Insights',
        patient=patient, summary=summary, analysis=analysis,
        risk=risk_report, picker=picker, patients=patients)


@ai_bp.route('/summary/<int:patient_id>')
@login_required
@roles_required(*CLINICAL)
def summary(patient_id):
    patient = _patient_or_404(patient_id)
    if not patient:
        return redirect(url_for('main.dashboard'))
    require_patient_access(patient)
    clinical = AIClinicalAssistant()
    risk = AIPatientRiskPrediction()
    rehab = AIRehabilitationAssistant()
    log_activity('AI_ANALYZE_PATIENT', 'patient', patient_id,
                 f'AI summary for {patient.user.full_name}')
    return render_template(
        'ai/summary.html', title='AI Patient Summary', patient=patient,
        summary=clinical.summarize_medical_history(patient.id),
        analysis=clinical.analyze_patient(patient.id),
        risk=risk.predict_risk(patient.id),
        rehab=rehab.analyze_progress(patient.id),
        **patient_safety_context(patient.id), today=utcnow().date())


@ai_bp.route('/diagnosis-support/<int:patient_id>', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def diagnosis_support(patient_id):
    patient = _patient_or_404(patient_id)
    if not patient:
        return redirect(url_for('main.dashboard'))
    require_patient_access(patient)
    result = None
    if request.method == 'POST':
        symptoms = request.form.get('symptoms', '')
        result = AIDiagnosisSupport().suggest_diagnoses(patient.id, symptoms)
        log_activity('AI_DIAGNOSIS_SUPPORT', 'patient', patient_id, symptoms)
    return render_template('ai/diagnosis_support.html',
                           title='AI Diagnosis Support', patient=patient,
                           result=result, **patient_safety_context(patient.id),
                           today=utcnow().date())


@ai_bp.route('/lab/<int:order_id>')
@login_required
@roles_required(*CLINICAL)
def lab_interpret(order_id):
    order = LabOrder.query.filter_by(id=order_id).first()
    if not order:
        flash('Lab order not found.', 'warning')
        return redirect(url_for('lab.orders'))
    require_patient_access(order.patient)
    result = AILaboratoryInterpretation().interpret_result(order_id)
    return render_template('ai/lab_interpretation.html',
                           title='AI Lab Interpretation', order=order,
                           patient=order.patient, result=result,
                           **patient_safety_context(order.patient_id),
                           today=utcnow().date())


@ai_bp.route('/radiology/<int:order_id>')
@login_required
@roles_required(*CLINICAL)
def radiology(order_id):
    order = RadiologyOrder.query.filter_by(id=order_id).first()
    if not order:
        flash('Radiology order not found.', 'warning')
        return redirect(url_for('radiology.orders'))
    require_patient_access(order.patient)
    result = AIRadiologyAssistant().analyze_study(order_id)
    return render_template('ai/radiology.html',
                           title='AI Radiology Summary', order=order,
                           patient=order.patient, result=result,
                           **patient_safety_context(order.patient_id),
                           today=utcnow().date())


@ai_bp.route('/prescription/<int:prescription_id>')
@login_required
@roles_required('Pharmacist', 'Doctor', 'Admin', 'SuperAdmin')
def prescription(prescription_id):
    rx = Prescription.query.filter_by(id=prescription_id).first()
    if not rx:
        flash('Prescription not found.', 'warning')
        return redirect(url_for('pharmacy.prescriptions'))
    require_patient_access(rx.patient)
    checker = AIPrescriptionChecker()
    engine = AIDrugInteractionEngine()
    check = checker.check_prescription(prescription_id)
    # Evaluate the current prescription against ALL of the patient's other
    # active medications (and this prescription's own items), so drug-drug
    # interactions are actually detected.
    active_ids = []
    for p in Prescription.query.filter_by(patient_id=rx.patient_id,
                                          status='Active').all():
        for item in p.items:
            if item.medication_id and item.medication_id not in active_ids:
                active_ids.append(item.medication_id)
    for item in rx.items:
        if item.medication_id and item.medication_id not in active_ids:
            active_ids.append(item.medication_id)
    interactions = engine.check_interactions(active_ids)
    return render_template('ai/prescription.html',
                           title='AI Prescription Check', rx=rx,
                           patient=rx.patient, check=check,
                           interactions=interactions,
                           **patient_safety_context(rx.patient_id),
                           today=utcnow().date())


@ai_bp.route('/rehab/<int:patient_id>')
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
def rehab(patient_id):
    patient = _patient_or_404(patient_id)
    if not patient:
        return redirect(url_for('main.dashboard'))
    require_patient_access(patient)
    rehab = AIRehabilitationAssistant()
    log_activity('AI_REHAB_ANALYSIS', 'patient', patient_id)
    return render_template('ai/rehab.html', title='AI Rehabilitation Insights',
                           patient=patient,
                           progress=rehab.analyze_progress(patient.id),
                           exercises=rehab.recommend_exercises(patient.id),
                           recovery=rehab.predict_recovery(patient.id),
                           optimize=rehab.optimize_treatment_plan(patient.id),
                           **patient_safety_context(patient.id),
                           today=utcnow().date())


@ai_bp.route('/analytics')
@login_required
@roles_required('Admin', 'SuperAdmin')
def analytics():
    result = AIHospitalAnalytics().forecast_occupancy()
    return render_template('ai/analytics.html', title='AI Hospital Analytics',
                           result=result)


# ---------------------------------------------------------------------------
# Integrated standalone AI models
# ---------------------------------------------------------------------------

@ai_bp.route('/medication-review/<int:patient_id>', methods=['GET', 'POST'])
@login_required
@roles_required('Pharmacist', 'Doctor', 'Nurse', 'Admin', 'SuperAdmin')
def medication_review(patient_id):
    """Gemini-powered comprehensive medication therapy review."""
    patient = _patient_or_404(patient_id)
    if not patient:
        return redirect(url_for('main.dashboard'))
    require_patient_access(patient)

    pharmacist = AIClinicalPharmacist()
    available = pharmacist.available()

    review = None
    if request.method == 'POST':
        review = pharmacist.review(patient)
        log_activity('AI_MEDICATION_REVIEW', 'patient', patient_id,
                     f'Medication review for {patient.user.full_name}')

    return render_template(
        'ai/medication_review.html', title='AI Medication Review',
        patient=patient, available=available,
        review=review,
        error=review.get('error') if review else None,
        **patient_safety_context(patient.id), today=utcnow().date())


@ai_bp.route('/fracture-detection', methods=['GET', 'POST'])
@login_required
@roles_required('Radiologist', 'Doctor', 'Nurse', 'Physiotherapist',
                'Dentist', 'Admin', 'SuperAdmin')
def fracture_detection():
    """YOLOv8 bone-fracture detection on uploaded X-rays."""
    from app.services.ai.fracture_detection import (
        detect_fracture, fracture_model_available,
    )
    result = None
    error = None
    patient = None
    if request.method == 'POST':
        storage, patient, doc, error = _resolve_image_input()
        if storage is not None:
            try:
                result = detect_fracture(storage)
                if 'error' in result:
                    error = result.pop('error', None)
                else:
                    _alert_on_positive_finding(result, patient, doc)
                    log_activity('AI_FRACTURE_DETECTION', 'radiology_order',
                                 patient.id if patient else 0,
                                 'Fracture detection run')
            finally:
                _close_storage(storage)

    available = fracture_model_available()
    doc = _load_doc(request.args.get('doc'))
    patient = patient or (doc.patient if doc else None)
    safety = patient_safety_context(patient.id) if patient else {}
    return render_template(
        'ai/fracture_detection.html', title='AI Fracture Detection',
        result=result, error=error, available=available,
        patient=patient,
        selected_doc=doc, **safety, today=utcnow().date())


@ai_bp.route('/tooth-segmentation', methods=['GET', 'POST'])
@login_required
@roles_required('Dentist', 'Radiologist', 'Nurse', 'Admin', 'SuperAdmin')
def tooth_segmentation():
    """U-Net dental (panoramic) tooth segmentation."""
    from app.services.ai.tooth_segmentation import (
        segment_tooth, tooth_model_available,
    )
    result = None
    error = None
    patient = None
    if request.method == 'POST':
        storage, patient, doc, error = _resolve_image_input()
        if storage is not None:
            try:
                result = segment_tooth(storage)
                if 'error' in result:
                    error = result.pop('error', None)
                else:
                    _alert_on_positive_finding(result, patient, doc)
                    log_activity('AI_TOOTH_SEGMENTATION', 'dental_record',
                                 patient.id if patient else 0,
                                 'Tooth segmentation run')
            finally:
                _close_storage(storage)

    available = tooth_model_available()
    doc = _load_doc(request.args.get('doc'))
    patient = patient or (doc.patient if doc else None)
    safety = patient_safety_context(patient.id) if patient else {}
    return render_template(
        'ai/tooth_segmentation.html', title='AI Tooth Segmentation',
        result=result, error=error, available=available,
        patient=patient,
        selected_doc=doc, **safety, today=utcnow().date())


@ai_bp.route('/skin-lesion-detection', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Dentist', 'Nurse', 'Admin', 'SuperAdmin')
def skin_lesion_detection():
    """ResNet-50 + EfficientNet-B0 ensemble for skin lesion classification."""
    from app.services.ai.skin_lesion_classification import (
        classify_skin_lesion, skin_model_available,
    )
    result = None
    error = None
    patient = None
    if request.method == 'POST':
        storage, patient, doc, error = _resolve_image_input()
        if storage is not None:
            try:
                result = classify_skin_lesion(storage)
                if 'error' in result:
                    error = result.pop('error', None)
                else:
                    _alert_on_positive_finding(result, patient, doc)
                    log_activity('AI_SKIN_LESION_DETECTION', 'patient',
                                 patient.id if patient else 0,
                                 'Skin lesion classification run')
            finally:
                _close_storage(storage)

    available = skin_model_available()
    doc = _load_doc(request.args.get('doc'))
    patient = patient or (doc.patient if doc else None)
    safety = patient_safety_context(patient.id) if patient else {}
    return render_template(
        'ai/skin_lesion_detection.html', title='AI Skin Lesion Detection',
        result=result, error=error, available=available,
        patient=patient,
        selected_doc=doc, **safety, today=utcnow().date())


@ai_bp.route('/media/<feature>/<kind>/<path:filename>')
@login_required
@roles_required('Radiologist', 'Doctor', 'Nurse', 'Physiotherapist',
                'Dentist', 'Admin', 'SuperAdmin')
def ai_media(feature, kind, filename):
    """Stream a private AI-generated image (uploaded X-ray or result/mask).

    These files live in the private UPLOAD_FOLDER (never under static/), so
    they have no public URL. Access is gated by login + the same roles that may
    use the AI tools, plus a strong-enough filename check to prevent traversal.

    ``kind`` is 'uploads' for the original image and 'results'/'masks' for the
    generated annotation/segmentation output.
    """
    if feature not in ('fracture', 'tooth', 'skin'):
        abort(404)
    base = current_app.config.get('UPLOAD_FOLDER') or 'var/uploads'
    feature_dir = {'fracture': os.path.join(base, 'ai', 'fracture'),
                   'tooth': os.path.join(base, 'ai', 'tooth'),
                   'skin': os.path.join(base, 'ai', 'skin')}[feature]
    sub = {'uploads': 'uploads', 'results': 'results', 'masks': 'uploads'}.get(kind)
    if sub is None:
        abort(404)
    safe = os.path.basename(filename)
    if safe != filename or not safe:
        abort(404)
    path = os.path.normpath(os.path.join(feature_dir, sub, safe))
    if not os.path.isfile(path):
        abort(404)
    return send_file(path)


# ---------------------------------------------------------------------------
# NEW: Gemini-powered AI features
# ---------------------------------------------------------------------------

@ai_bp.route('/soap-notes/<int:patient_id>', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def soap_notes(patient_id):
    """Generate AI-powered SOAP clinical notes."""
    patient = _patient_or_404(patient_id)
    if not patient:
        return redirect(url_for('main.dashboard'))
    require_patient_access(patient)
    note = None
    if request.method == 'POST':
        context = request.form.get('clinical_context', '')
        generator = AIClinicalNotes()
        note = generator.generate_soap(patient.id, context)
        log_activity('AI_SOAP_NOTES', 'patient', patient_id,
                     f'SOAP note generated for {patient.user.full_name}')
    return render_template('ai/soap_notes.html',
                           title='AI Clinical Notes (SOAP)',
                           patient=patient, note=note,
                           available=gemini_available(),
                           **patient_safety_context(patient.id),
                           today=utcnow().date())


@ai_bp.route('/smart-orders/<int:patient_id>', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def smart_orders(patient_id):
    """Generate AI-powered smart order sets."""
    patient = _patient_or_404(patient_id)
    if not patient:
        return redirect(url_for('main.dashboard'))
    require_patient_access(patient)
    order_set = None
    if request.method == 'POST':
        diagnosis = request.form.get('diagnosis_text', '')
        engine = AISmartOrders()
        order_set = engine.generate_order_set(patient.id, diagnosis)
        log_activity('AI_SMART_ORDERS', 'patient', patient_id,
                     f'Order set generated for {patient.user.full_name}')
    return render_template('ai/smart_orders.html',
                           title='AI Smart Order Sets',
                           patient=patient, order_set=order_set,
                           available=gemini_available(),
                           **patient_safety_context(patient.id),
                           today=utcnow().date())


@ai_bp.route('/patient-communication/<int:patient_id>', methods=['GET', 'POST'])
@login_required
@roles_required(*CLINICAL)
def patient_communication(patient_id):
    """Generate AI-powered patient-friendly communication."""
    patient = _patient_or_404(patient_id)
    if not patient:
        return redirect(url_for('main.dashboard'))
    require_patient_access(patient)
    communication = None
    if request.method == 'POST':
        language = request.form.get('language', 'en')
        topic = request.form.get('topic', 'full_summary')
        gen = AIPatientCommunication()
        communication = gen.generate_communication(
            patient.id, language=language, topic=topic)
        log_activity('AI_PATIENT_COMM', 'patient', patient_id,
                     f'Patient communication generated for {patient.user.full_name}')
    return render_template('ai/patient_communication.html',
                           title='AI Patient Communication',
                           patient=patient, communication=communication,
                           available=gemini_available(),
                           **patient_safety_context(patient.id),
                           today=utcnow().date())


@ai_bp.route('/medical-coding/<int:patient_id>', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def medical_coding(patient_id):
    """AI-powered ICD-10 medical coding suggestions."""
    patient = _patient_or_404(patient_id)
    if not patient:
        return redirect(url_for('main.dashboard'))
    require_patient_access(patient)
    result = None
    if request.method == 'POST':
        mode = request.form.get('mode', 'text')
        assistant = AIMedicalCodingAssistant()
        if mode == 'diagnoses':
            result = assistant.suggest_codes_from_diagnosis(patient.id)
        else:
            text = request.form.get('clinical_text', '')
            result = assistant.suggest_code(text, patient_id=patient.id)
        log_activity('AI_MEDICAL_CODING', 'patient', patient_id,
                     f'Medical coding for {patient.user.full_name}')
    return render_template('ai/medical_coding.html',
                           title='AI Medical Coding',
                           patient=patient, result=result,
                           available=gemini_available(),
                           **patient_safety_context(patient.id),
                           today=utcnow().date())


@ai_bp.route('/clinical-alerts')
@login_required
@roles_required('Doctor', 'Nurse', 'Admin', 'SuperAdmin')
def clinical_alerts():
    """View and manage AI-generated clinical alerts."""
    from app.models import ClinicalAlert
    engine = AIClinicalAlertEngine()
    alerts = []
    # Get alerts for current user's patients or all if admin
    is_admin = current_user.has_any_role('Admin', 'SuperAdmin')
    if is_admin:
        alerts = ClinicalAlert.query.filter_by(
            status='OPEN').order_by(
            ClinicalAlert.created_at.desc()).limit(50).all()
    else:
        # Staff: show alerts for their patients
        from app.access import accessible_patient_ids
        pids = accessible_patient_ids()
        if pids:
            alerts = ClinicalAlert.query.filter(
                ClinicalAlert.patient_id.in_(pids),
                ClinicalAlert.status == 'OPEN'
            ).order_by(ClinicalAlert.created_at.desc()).limit(50).all()
    return render_template('ai/clinical_alerts.html',
                           title='AI Clinical Alerts',
                           alerts=alerts)


@ai_bp.route('/clinical-alerts/scan', methods=['POST'])
@login_required
@roles_required('Admin', 'SuperAdmin')
def scan_alerts():
    """Run a full alert scan on all active patients."""
    engine = AIClinicalAlertEngine()
    generated = engine.scan_all_active_patients()
    for alert_data in generated:
        engine.create_alert(alert_data)
    flash(f'Alert scan complete. {len(generated)} alerts generated.', 'info')
    return redirect(url_for('ai.clinical_alerts'))


@ai_bp.route('/clinical-alerts/<int:alert_id>/read', methods=['POST'])
@login_required
def mark_alert_read(alert_id):
    """Acknowledge an OPEN clinical alert (move it out of the active queue)."""
    from app.models import ClinicalAlert
    from app.services.alerts import acknowledge
    alert = db.session.get(ClinicalAlert, alert_id)
    if alert:
        try:
            acknowledge(alert)
            db.session.commit()
        except ValueError:
            db.session.rollback()
    return redirect(url_for('ai.clinical_alerts'))


@ai_bp.route('/ai-dashboard')
@login_required
@roles_required('Doctor', 'Nurse', 'Admin', 'SuperAdmin')
def ai_dashboard():
    """Central AI dashboard showing all AI features and recent recommendations."""
    from app.models import AIRecommendation, ClinicalAlert, Patient
    recent_recs = AIRecommendation.query.order_by(
        AIRecommendation.created_at.desc()).limit(20).all()
    unread_alerts = ClinicalAlert.query.filter_by(status='OPEN').count()
    total_recs = AIRecommendation.query.count()
    applied_recs = AIRecommendation.query.filter_by(is_applied=True).count()

    # Stats by type
    rec_types = db.session.query(
        AIRecommendation.recommendation_type,
        db.func.count(AIRecommendation.id)
    ).group_by(AIRecommendation.recommendation_type).all()

    return render_template('ai/ai_dashboard.html',
                           title='AI Command Center',
                           recent_recs=recent_recs,
                           unread_alerts=unread_alerts,
                           total_recs=total_recs,
                           applied_recs=applied_recs,
                           rec_types=rec_types,
                           gemini_available=gemini_available())
