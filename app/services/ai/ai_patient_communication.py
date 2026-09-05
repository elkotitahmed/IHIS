"""AI Patient Communication Generator.

Creates patient-friendly summaries of their conditions, treatment plans,
and post-visit instructions — in both English and Arabic.
"""
from datetime import date
from app import db
from app.models import (Patient, Diagnosis, Prescription, LabOrder, LabResult,
                        VitalSign, Admission)
from app.services.ai.gemini_base import AIServiceError, GeminiBase, _collect_patient_context

SYSTEM_PROMPT = (
    "You are a patient education specialist. Create clear, compassionate, "
    "patient-friendly communication materials. Use plain language (6th-8th "
    "grade reading level). Avoid medical jargon or explain it simply. "
    "Be empathetic and supportive. Include actionable self-care instructions. "
    "When the patient's preferred language is Arabic, provide the response "
    "in Arabic. Otherwise use English. Always include a disclaimer that this "
    "does not replace professional medical advice."
)

OUTPUT_FORMAT = (
    "Format your response as JSON:\n"
    '{"condition_summary": "A plain-language explanation of the condition", '
    '"treatment_explanation": "What the treatment does and why it matters", '
    '"medication_guide": [{"name": "...", "purpose": "...", "instructions": "...", '
    '"side_effects_to_watch": "..."}], '
    '"self_care_instructions": ["..."], '
    '"warning_signs": ["When to seek immediate medical attention: ..."], '
    '"follow_up_instructions": "...", '
    '"dietary_notes": "...", '
    '"patient_friendly_title": "...", '
    '"disclaimer": "..."}'
)


class AIPatientCommunication(GeminiBase):
    """LLM-powered patient communication generator."""

    def __init__(self):
        super().__init__(system_prompt=SYSTEM_PROMPT,
                         temperature=0.4, max_tokens=4000)

    def generate_communication(self, patient_id, language="en", topic="full_summary"):
        if not self.available():
            return {'available': False,
                    'error': 'Patient communication requires Gemini API key.'}

        patient = db.session.get(Patient, patient_id)
        if not patient:
            return {'error': 'Patient not found'}

        ctx = _collect_patient_context(patient)

        lang_instruction = (
            "Provide the entire response in Arabic (Modern Standard Arabic)."
            if language == 'ar'
            else "Provide the response in clear English."
        )

        topic_prompts = {
            'full_summary': 'Generate a complete summary of the patient\'s current health status, conditions, and treatment plan.',
            'medications': 'Focus on explaining each current medication — what it does, how to take it, and what side effects to watch for.',
            'post_visit': 'Create post-visit instructions based on today\'s encounter.',
            'diagnosis_explanation': 'Explain the patient\'s diagnoses in simple, understandable language.',
        }
        topic_prompt = topic_prompts.get(topic, topic_prompts['full_summary'])

        # No direct identifiers leave the system: the clinical context is
        # de-identified and the patient is addressed generically.
        prompt = (
            f"{ctx}\n\n"
            f"{lang_instruction}\n\n"
            f"Task: {topic_prompt}\n\n"
            f"Address the patient directly and respectfully without using a name.\n"
            f"Preferred language: {'Arabic' if language == 'ar' else 'English'}\n\n"
            f"{OUTPUT_FORMAT}"
        )

        try:
            result = self._call_gemini_json(prompt)
            if 'raw_text' in result:
                return {'available': True, 'raw': result['raw_text'],
                        'source': 'gemini'}
            result['available'] = True
            result['source'] = 'gemini'
            result['language'] = language
            self._log_communication(patient_id, topic)
            return result
        except Exception as e:  # noqa: BLE001
            return {'available': True, 'error': _public_error(e)}

    def _log_communication(self, patient_id, topic):
        try:
            from app.models import AIRecommendation
            rec = AIRecommendation(
                patient_id=patient_id,
                recommendation_type='patient_communication',
                content=f'Patient communication generated: {topic}',
                confidence_score=0.9)
            db.session.add(rec)
            db.session.commit()
        except Exception:
            db.session.rollback()


def _public_error(exc):
    return str(exc) if isinstance(exc, AIServiceError) else 'The AI service could not complete the request.'
