"""AI layer routes: expose clinical decision-support assistants."""
import os
import re
import time

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.models import (
    Patient, Doctor, PhysicalTherapist, LabOrder, RadiologyOrder,
    Prescription, Appointment,
)
from app.routes.decorators import roles_required, log_activity
from app.access import require_patient_access
from app.services.ai import (
    get_assistant, AIClinicalAssistant, AIDiagnosisSupport,
    AIPrescriptionChecker, AIDrugInteractionEngine, AILaboratoryInterpretation,
    AIRadiologyAssistant, AIPatientRiskPrediction, AIRehabilitationAssistant,
    AIHospitalAnalytics, AIClinicalPharmacist,
)

ai_bp = Blueprint('ai', __name__)

CLINICAL = ('Doctor', 'Admin', 'SuperAdmin', 'Nurse', 'Physiotherapist',
            'LabTechnician', 'Radiologist', 'Pharmacist')


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
    if not patient:
        flash('Please complete your patient profile first.', 'warning')
        return redirect(url_for('patient.profile'))
    clinical = AIClinicalAssistant()
    risk = AIPatientRiskPrediction()
    summary = clinical.summarize_medical_history(patient.id)
    analysis = clinical.analyze_patient(patient.id)
    risk_report = risk.predict_risk(patient.id)
    return render_template(
        'ai/health_insights.html', title='AI Health Insights',
        patient=patient, summary=summary, analysis=analysis,
        risk=risk_report)


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
        rehab=rehab.analyze_progress(patient.id))


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
                           result=result)


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
                           result=result)


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
                           result=result)


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
                           check=check, interactions=interactions)


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
                           optimize=rehab.optimize_treatment_plan(patient.id))


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
        error=review.get('error') if review else None)


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
    if request.method == 'POST':
        if 'file' not in request.files or not request.files['file'].filename:
            error = 'Please select an image to analyze.'
        else:
            result = detect_fracture(request.files['file'])
            if 'error' in result:
                error = result.pop('error', None)
            else:
                log_activity('AI_FRACTURE_DETECTION', 'radiology_order', 0,
                             'Fracture detection run')

    available = fracture_model_available()
    return render_template(
        'ai/fracture_detection.html', title='AI Fracture Detection',
        result=result, error=error, available=available)


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
    if request.method == 'POST':
        if 'file' not in request.files or not request.files['file'].filename:
            error = 'Please select an image to analyze.'
        else:
            result = segment_tooth(request.files['file'])
            if 'error' in result:
                error = result.pop('error', None)
            else:
                log_activity('AI_TOOTH_SEGMENTATION', 'dental_record', 0,
                             'Tooth segmentation run')

    available = tooth_model_available()
    return render_template(
        'ai/tooth_segmentation.html', title='AI Tooth Segmentation',
        result=result, error=error, available=available)
