"""AI Clinical Notes (SOAP) Generator.

Generates structured SOAP notes (Subjective, Objective, Assessment, Plan)
from clinical data — saving physicians 10-15 minutes per encounter.
"""
from datetime import datetime
from app import db
from app.models import (Patient, Diagnosis, VitalSign, LabOrder, LabResult,
                        Prescription, MedicalRecord)
from app.services.ai.gemini_base import GeminiBase, _collect_patient_context

SYSTEM_PROMPT = (
    "You are an expert clinical documentation assistant. Generate a "
    "comprehensive SOAP note based on the patient's clinical data. "
    "The note should follow standard SOAP format and be ready for "
    "physician review. Use professional medical language. Include all "
    "relevant clinical details. The Assessment section should include "
    "differential diagnoses ranked by likelihood. The Plan should be "
    "actionable and specific. Always include appropriate follow-up "
    "instructions."
)

OUTPUT_FORMAT = (
    "Format your response as JSON:\n"
    '{"subjective": {"chief_complaint": "...", "history_of_present_illness": "...", '
    '"review_of_systems": "...", "past_medical_history": "...", '
    '"medications": "...", "allergies": "..."}, '
    '"objective": {"vital_signs": "...", "physical_exam": "Not documented - awaiting physician exam", '
    '"lab_results": "...", "imaging": "..."}, '
    '"assessment": {"primary_dx": "...", "differential_dx": ["..."], '
    '"clinical_reasoning": "..."}, '
    '"plan": {"diagnostic_workup": ["..."], "therapeutic_interventions": ["..."], '
    '"medications": ["..."], "patient_education": ["..."], '
    '"follow_up": "...", "disposition": "..."}, '
    '"quality_flags": ["..."], '
    '"disclaimer": "AI-generated note requires physician review and signature."}'
)


class AIClinicalNotes(GeminiBase):
    """LLM-powered SOAP note generator."""

    def __init__(self):
        super().__init__(system_prompt=SYSTEM_PROMPT,
                         temperature=0.3, max_tokens=5000)

    def generate_soap(self, patient_id, clinical_context=""):
        if not self.available():
            return {'available': False,
                    'error': 'AI note generation requires Gemini API key.',
                    'note': None}

        patient = db.session.get(Patient, patient_id)
        if not patient:
            return {'error': 'Patient not found'}

        ctx = _collect_patient_context(patient)

        # Add existing medical records for context
        records = MedicalRecord.query.filter_by(
            patient_id=patient_id).order_by(
            MedicalRecord.visit_date.desc()).limit(3).all()
        records_text = '\n'.join(
            f"  - {r.visit_date.strftime('%Y-%m-%d') if r.visit_date else 'N/A'}: "
            f"{r.diagnosis or ''} | {(r.clinical_notes or '')[:200]}"
            for r in records) if records else '  - No prior records'

        prompt = (
            f"{ctx}\n\n"
            f"RECENT CLINICAL ENCOUNTERS:\n{records_text}\n\n"
            f"ADDITIONAL CLINICAL CONTEXT FROM PHYSICIAN:\n"
            f"  {clinical_context or 'None provided'}\n\n"
            f"Generate a complete SOAP note for today's encounter.\n"
            f"{OUTPUT_FORMAT}"
        )

        try:
            result = self._call_gemini_json(prompt)
            if 'raw_text' in result:
                return {'available': True, 'raw': result['raw_text'],
                        'source': 'gemini', 'note': None}
            result['available'] = True
            result['source'] = 'gemini'
            result['generated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            result['patient_name'] = patient.user.full_name if patient.user else 'Patient'
            self._log_note(patient_id)
            return result
        except Exception as e:  # noqa: BLE001
            from app.services.ai.gemini_base import AIServiceError
            msg = str(e) if isinstance(e, AIServiceError) else 'The AI service could not complete the request.'
            return {'available': True, 'error': msg, 'note': None}

    def _log_note(self, patient_id):
        try:
            from app.models import AIRecommendation
            rec = AIRecommendation(
                patient_id=patient_id,
                recommendation_type='clinical_notes',
                content='SOAP note generated',
                confidence_score=0.9)
            db.session.add(rec)
            db.session.commit()
        except Exception:
            db.session.rollback()
