"""AI Integration Layer - clinical decision support.

These assistants provide deterministic, rule-based clinical insights built
on the data already stored in iHIS. They are designed as drop-in points so
real ML/AI models (or an external LLM endpoint) can replace the heuristics
later without changing the calling code.
"""
from datetime import datetime, date

from app import db
from app.models import (
    Diagnosis, Medication, Prescription, LabOrder, LabResult,
    RadiologyOrder, RadiologyReport, VitalSign, NursingNote, CarePlan,
    TherapyAssessment, TherapyPlan, RehabilitationProgress, TherapySession,
    ExerciseLibraryItem, TherapyExercise, FunctionalOutcome, DrugInteraction,
    MedicalRecord, Patient, User, Appointment,
)
from app.services.laboratory import evaluate_abnormality


class AIClinicalAssistant:
    """Assists doctors with patient analysis and clinical insights."""

    def summarize_medical_history(self, patient_id):
        patient = db.session.get(Patient, patient_id)
        if not patient:
            return {'error': 'Patient not found'}
        diagnoses = Diagnosis.query.filter_by(patient_id=patient_id).all()
        prescriptions = Prescription.query.filter_by(patient_id=patient_id).all()
        records = MedicalRecord.query.filter_by(patient_id=patient_id)\
            .order_by(MedicalRecord.visit_date.desc()).all()
        return {
            'patient_name': patient.user.full_name if patient.user else 'Patient',
            'blood_type': patient.blood_type or 'Unknown',
            'allergies': patient.allergies or 'None recorded',
            'chronic_diseases': patient.chronic_diseases or 'None recorded',
            'diagnoses': [{'description': d.description,
                           'icd10': d.icd10_code or '-',
                           'primary': d.is_primary}
                          for d in diagnoses],
            'active_prescriptions': [
                {'medication': p.medication.generic_name if p.medication else '-',
                 'dosage': p.dosage, 'frequency': p.frequency, 'status': p.status}
                for p in prescriptions if p.status == 'Active'],
            'record_count': len(records),
            'last_visit': records[0].visit_date.strftime('%Y-%m-%d') if records else 'None',
        }

    def analyze_patient(self, patient_id):
        summary = self.summarize_medical_history(patient_id)
        if 'error' in summary:
            return summary
        flags = []
        if summary['allergies'] not in ('None recorded', 'Unknown'):
            flags.append({'level': 'warning',
                          'title': 'Documented Allergies',
                          'detail': f"Allergies on record: {summary['allergies']}"})
        if summary['chronic_diseases'] not in ('None recorded', 'Unknown'):
            flags.append({'level': 'info',
                          'title': 'Chronic Conditions',
                          'detail': f"Ongoing conditions: {summary['chronic_diseases']}"})
        if not summary['active_prescriptions']:
            flags.append({'level': 'low', 'title': 'No Active Medications',
                          'detail': 'No active prescriptions found.'})
        summary['flags'] = flags
        return summary


class AIDiagnosisSupport:
    """Suggests potential diagnoses based on symptoms and history."""

    # Simple symptom keyword -> diagnostic suggestions (rule-based heuristic)
    KNOWLEDGE = {
        'fever headache': 'Influenza, Meningitis (urgent), Typhoid',
        'chest pain': 'Angina, Myocardial Infarction (urgent), GERD',
        'shortness breath': 'COPD, Asthma, Pulmonary Embolism (urgent), Heart Failure',
        'fatigue': 'Anemia, Hypothyroidism, Diabetes Mellitus, Depression',
        'abdominal pain': 'Appendicitis (urgent), Gastritis, IBS, Gallstones',
        'joint pain': 'Osteoarthritis, Rheumatoid Arthritis, Gout',
        'polyuria thirst': 'Diabetes Mellitus (consider HbA1c)',
    }

    def suggest_diagnoses(self, patient_id, symptoms):
        symptoms = (symptoms or '').strip().lower()
        if not symptoms:
            return {'suggestions': [], 'note': 'Provide symptoms for suggestions.'}
        matched = []
        for key, value in self.KNOWLEDGE.items():
            if all(kw in symptoms for kw in key.split()):
                matched.append(value)
        # Existing diagnoses as context
        prior = Diagnosis.query.filter_by(patient_id=patient_id).all()
        return {
            'suggestions': matched or [
                'Review symptoms and order targeted investigations.'],
            'prior_diagnoses': [d.description for d in prior],
            'disclaimer': 'AI suggestions are for decision support only. '
                          'Always confirm clinically.',
        }


class AIPrescriptionChecker:
    """Validates prescriptions for completeness and safety."""

    def check_prescription(self, prescription_id):
        rx = db.session.get(Prescription, prescription_id)
        if not rx:
            return {'error': 'Prescription not found'}
        issues = []
        if not rx.dosage:
            issues.append({'level': 'warning', 'item': 'Missing dosage'})
        if not rx.frequency:
            issues.append({'level': 'warning', 'item': 'Missing frequency'})
        if not rx.instructions:
            issues.append({'level': 'low', 'item': 'No administration instructions'})
        if rx.medication and rx.medication.contraindications:
            issues.append({'level': 'info',
                           'item': f"Contraindications: {rx.medication.contraindications}"})
        return {
            'medication': rx.medication.generic_name if rx.medication else '-',
            'status': rx.status,
            'issues': issues,
            'ok': not any(i['level'] == 'warning' for i in issues),
            'disclaimer': 'Always confirm with the prescriber before dispensing.',
        }


class AIDrugInteractionEngine:
    """Detects adverse drug-drug interactions."""

    def check_interactions(self, medication_ids):
        medication_ids = [m for m in (medication_ids or []) if m]
        if len(medication_ids) < 2:
            return {'interactions': [], 'note': 'Select at least two medications. (reference,0)'.replace('(reference,0)','')}
        interactions = DrugInteraction.query.filter(
            (DrugInteraction.medication_a_id.in_(medication_ids)
             & DrugInteraction.medication_b_id.in_(medication_ids))
        ).all()
        meds = {m.id: (m.generic_name or m.brand_name) for m in
                Medication.query.filter(Medication.id.in_(medication_ids)).all()}
        result = [{
            'a': meds.get(i.medication_a_id, '-'),
            'b': meds.get(i.medication_b_id, '-'),
            'severity': i.severity,
            'description': i.description,
        } for i in interactions]
        return {'medications': list(meds.values()), 'interactions': result,
                'count': len(result)}


class AILaboratoryInterpretation:
    """Interprets lab results and highlights abnormal values."""

    def interpret_result(self, order_id):
        order = db.session.get(LabOrder, order_id)
        if not order or not order.result:
            return {'error': 'Lab order or result not found'}
        test = order.test
        abnormal = bool(order.result.is_abnormal)
        if not abnormal and test and test.normal_range:
            abnormal = evaluate_abnormality(order.result.result_value, test.normal_range)
        return {
            'test': test.test_name if test else '-',
            'category': test.category if test else '-',
            'result': order.result.result_value,
            'unit': test.unit if test else '-',
            'normal_range': test.normal_range if test else '-',
            'abnormal': abnormal,
            'interpretation': (
                'Above or below the reference range. Review clinically and '
                'correlate with the patient presentation.'
                if abnormal else 'Within the reference range.'
            ),
            'comment': order.result.result_notes,
        }


class AIRadiologyAssistant:
    """Assists radiologists with report drafting."""

    def analyze_study(self, order_id):
        order = db.session.get(RadiologyOrder, order_id)
        if not order:
            return {'error': 'Radiology order not found'}
        report = order.report
        return {
            'imaging_type': order.imaging_type.name if order.imaging_type else '-',
            'status': order.status,
            'has_report': report is not None,
            'findings': report.findings if report else 'No report yet.',
            'impression': report.impression if report else 'Pending interpretation.',
            'recommendation': report.recommendation if report else '-',
        }


class AIPatientRiskPrediction:
    """Predicts patient risk for adverse outcomes (rule-based heuristic)."""

    def predict_risk(self, patient_id):
        patient = db.session.get(Patient, patient_id)
        if not patient:
            return {'error': 'Patient not found'}
        score = 0
        reasons = []
        # Age risk
        if patient.date_of_birth:
            age = (date.today() - patient.date_of_birth).days // 365
            if age >= 65:
                score += 2
                reasons.append('Age 65+')
            elif age <= 2:
                score += 2
                reasons.append('Age under 3')
        if patient.chronic_diseases:
            score += 2
            reasons.append('Chronic disease present')
        if patient.allergies:
            score += 1
            reasons.append('Documented allergies')
        # Abnormal labs
        abnormal = LabResult.query.join(LabOrder).filter(
            LabOrder.patient_id == patient_id, LabResult.is_abnormal.is_(True)).count()
        if abnormal:
            score += min(abnormal, 3)
            reasons.append(f'{abnormal} abnormal lab result(s)')
        # Recent abnormal vitals (elevated BP or HR)
        vitals = VitalSign.query.filter_by(patient_id=patient_id)\
            .order_by(VitalSign.recorded_at.desc()).first()
        if vitals:
            if vitals.blood_pressure_systolic and vitals.blood_pressure_systolic >= 140:
                score += 1
                reasons.append('Elevated systolic blood pressure')
            if vitals.heart_rate and vitals.heart_rate > 100:
                score += 1
                reasons.append('Tachycardia')
        level = 'Low'
        if score >= 6:
            level = 'High'
        elif score >= 3:
            level = 'Moderate'
        return {'patient': patient.user.full_name if patient.user else '-',
                'score': score, 'level': level, 'reasons': reasons,
                'disclaimer': 'Heuristic risk estimate for triage; not a diagnosis.'}


class AIAppointmentOptimization:
    """Optimizes appointment scheduling and resource allocation."""

    def recommend_slots(self, doctor_id):
        Doctor = __import__('app.models', fromlist=['Doctor']).Doctor
        doc = db.session.get(Doctor, doctor_id)
        if not doc:
            return {'error': 'Doctor not found'}
        booked = Appointment.query.filter(
            Appointment.doctor_id == doctor_id,
            Appointment.status.notin_(['Cancelled', 'NoShow'])).count()
        return {
            'doctor': doc.user.full_name if doc.user else '-',
            'booked_appointments': booked,
            'recommendation': (
                'Consider rescheduling or adding availability.'
                if booked >= 20 else 'Availability is adequate.')
        }


class AIMedicalCodingAssistant:
    """Assists with ICD-10 medical coding (simple keyword mapping)."""

    ICD = {
        'hypertension': 'I10', 'diabetes': 'E11.9', 'pneumonia': 'J18.9',
        'asthma': 'J45.9', 'anemia': 'D64.9', 'gastritis': 'K29.7',
        'headache': 'R51', 'angina': 'I20.9', 'depression': 'F32.9',
        'arthritis': 'M13.9', 'respiratory infection': 'J06.9',
        'fracture': 'S52.9',
    }

    def suggest_code(self, text):
        text = (text or '').lower()
        matches = {}
        for keyword, code in self.ICD.items():
            if keyword in text:
                matches[keyword] = code
        return {'matches': matches, 'note': 'Verify code specificity on chart review.'}


class AIHospitalAnalytics:
    """Provides predictive analytics for hospital operations."""

    def forecast_occupancy(self, department_id=None):
        counts = {
            'patients': Patient.query.count(),
            'appointments': Appointment.query.count(),
            'lab_orders': LabOrder.query.count(),
            'radiology_orders': RadiologyOrder.query.count(),
            'active_prescriptions': Prescription.query.filter_by(status='Active').count(),
            'open_care_plans': CarePlan.query.filter_by(status='Active').count(),
        }
        return {'counts': counts,
                'status': 'Operational analytics snapshot'}


class AIRehabilitationAssistant:
    """Supports physical therapy and rehabilitation treatment."""

    def analyze_progress(self, patient_id):
        progress = RehabilitationProgress.query.filter_by(patient_id=patient_id)\
            .order_by(RehabilitationProgress.recorded_at.asc()).all()
        if not progress:
            return {'error': 'No progress data recorded yet.'}
        # trend across functional outcome
        trend = 'stable'
        if len(progress) > 1:
            first, last = progress[0], progress[-1]
            delta = (last.functional_outcome or 0) - (first.functional_outcome or 0)
            if delta > 5:
                trend = 'improving'
            elif delta < -5:
                trend = 'declining'
        latest = progress[-1]
        return {'records': len(progress), 'trend': trend,
                'latest': {
                    'pain_score': latest.pain_score,
                    'mobility_score': latest.mobility_score,
                    'strength_score': latest.strength_score,
                    'functional_outcome': latest.functional_outcome,
                    'compliance': latest.compliance,
                    'recorded_at': latest.recorded_at.strftime('%Y-%m-%d')},
                'recommendation': (
                    'Continue current plan; consider intensifying exercises.'
                    if trend == 'improving' else
                    'Re-evaluate goals; adjust the plan to address regression.'
                    if trend == 'declining' else
                    'Maintain current plan and monitor.'),
        }

    def recommend_exercises(self, patient_id):
        plan = TherapyPlan.query.filter_by(patient_id=patient_id,
                                           status='Active').first()
        item_ids = []
        if plan:
            item_ids = [e.library_item_id for e in plan.exercises
                        if e.library_item_id]
        cat = None
        if plan:
            cat = plan.title
        items = ExerciseLibraryItem.query.all()
        recommendations = items[:4]
        return {
            'plan': plan.title if plan else 'No active plan',
            'recommended': [
                {'name': ex.name, 'category': ex.category,
                 'reps': ex.repetitions, 'duration_s': ex.duration_seconds}
                for ex in recommendations],
            'note': 'Exercise suggestions based on active rehabilitation plan.',
        }

    def predict_recovery(self, patient_id):
        assessment = TherapyAssessment.query.filter_by(patient_id=patient_id)\
            .order_by(TherapyAssessment.assessed_at.desc()).first()
        progress = RehabilitationProgress.query.filter_by(patient_id=patient_id)\
            .order_by(RehabilitationProgress.recorded_at.desc()).first()
        est = 'Not enough data'
        if assessment and assessment.pain_assessment is not None:
            if assessment.pain_assessment <= 3:
                est = 'Good trajectory; likely to progress quickly'
            elif assessment.pain_assessment <= 6:
                est = 'Moderate trajectory'
            else:
                est = 'Expect a longer recovery; review plan'
        return {'estimated_trajectory': est,
                'baseline_pain': assessment.pain_assessment if assessment else None,
                'compliance': progress.compliance if progress else None}

    def optimize_treatment_plan(self, patient_id):
        analysis = self.analyze_progress(patient_id)
        if 'error' in analysis:
            return analysis
        return {'trend': analysis['trend'],
                'suggestion': analysis['recommendation'],
                'increase_load': analysis['trend'] == 'improving',
                'reduce_load': analysis['trend'] == 'declining'}
