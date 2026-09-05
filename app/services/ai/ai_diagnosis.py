"""Gemini-powered differential diagnosis assistant.

Replaces the heuristic keyword-matching stub with an LLM that analyzes
the full clinical context (symptoms, history, vitals, labs) and returns
ranked differential diagnoses with confidence levels and next-step
investigations.
"""
from datetime import date
from app import db
from app.models import Diagnosis, Patient, VitalSign, LabOrder, LabResult, Prescription
from app.services.ai.gemini_base import GeminiBase, _collect_patient_context

SYSTEM_PROMPT = (
    "You are a board-certified physician and expert diagnostician. "
    "Analyze the patient's clinical presentation and provide a ranked "
    "differential diagnosis list. For each differential, provide: "
    "the diagnosis name, ICD-10 code (if applicable), confidence level "
    "(High/Moderate/Low), clinical reasoning, and recommended next "
    "investigations. Consider the patient's age, gender, comorbidities, "
    "medications, vital signs, and lab results. Always consider dangerous "
    "conditions first. Your differential should be evidence-based and "
    "follow clinical reasoning principles. Note: these are decision "
    "support suggestions only — the treating physician makes all final "
    "diagnostic and therapeutic decisions."
)

OUTPUT_FORMAT = (
    "Format your response as JSON with the following structure:\n"
    '{"differentials": [{"rank": 1, "diagnosis": "...", "icd10": "...", '
    '"confidence": "High|Moderate|Low", "reasoning": "...", '
    '"key_findings": ["..."], "next_steps": ["..."], "urgency": "Urgent|Routine|Non-Urgent"}], '
    '"clinical_reasoning": "...", "red_flags": ["..."], '
    '"disclaimer": "..."}'
)


class AIDiagnosisSupport(GeminiBase):
    """LLM-powered differential diagnosis assistant."""

    def __init__(self):
        super().__init__(system_prompt=SYSTEM_PROMPT,
                         temperature=0.3, max_tokens=4000)

    def suggest_diagnoses(self, patient_id, symptoms):
        if not self.available():
            return self._fallback_heuristic(patient_id, symptoms)

        patient = db.session.get(Patient, patient_id)
        if not patient:
            return {'error': 'Patient not found'}

        ctx = _collect_patient_context(patient)
        prompt = (
            f"{ctx}\n\n"
            f"Clinician-reported symptoms/clinical presentation:\n"
            f"  {symptoms}\n\n"
            f"{OUTPUT_FORMAT}"
        )

        try:
            result = self._call_gemini_json(prompt)
            if 'raw_text' in result:
                return {'available': True, 'raw': result['raw_text'],
                        'source': 'gemini'}
            result['available'] = True
            result['source'] = 'gemini'
            # Log to audit trail
            self._log_diagnosis(patient_id, symptoms, result)
            return result
        except Exception as e:
            return self._fallback_heuristic(patient_id, symptoms, str(e))

    def _fallback_heuristic(self, patient_id, symptoms, error=None):
        """Fallback to keyword matching when Gemini unavailable."""
        symptoms = (symptoms or '').strip().lower()
        KNOWLEDGE = {
            'fever headache': 'Influenza, Meningitis (urgent), Typhoid',
            'chest pain': 'Angina, Myocardial Infarction (urgent), GERD',
            'shortness breath': 'COPD, Asthma, Pulmonary Embolism (urgent), Heart Failure',
            'fatigue': 'Anemia, Hypothyroidism, Diabetes Mellitus, Depression',
            'abdominal pain': 'Appendicitis (urgent), Gastritis, IBS, Gallstones',
            'joint pain': 'Osteoarthritis, Rheumatoid Arthritis, Gout',
            'polyuria thirst': 'Diabetes Mellitus (consider HbA1c)',
        }
        matched = []
        for key, value in KNOWLEDGE.items():
            if all(kw in symptoms for kw in key.split()):
                matched.append({'diagnosis': d.strip(), 'icd10': '',
                                'confidence': 'Moderate', 'reasoning': f'Matched symptom pattern: {key}',
                                'key_findings': [key], 'next_steps': ['Clinical correlation recommended'],
                                'urgency': 'Routine'}
                               for d in value.split(','))
        prior = Diagnosis.query.filter_by(patient_id=patient_id).all()
        return {
            'available': False,
            'differentials': matched or [{'diagnosis': 'Review symptoms and order targeted investigations.',
                                          'icd10': '', 'confidence': 'Low',
                                          'reasoning': 'No pattern match found.',
                                          'key_findings': [], 'next_steps': [],
                                          'urgency': 'Routine'}],
            'prior_diagnoses': [d.description for d in prior],
            'clinical_reasoning': 'Heuristic keyword matching (Gemini unavailable)',
            'red_flags': [],
            'source': 'heuristic',
            'disclaimer': 'AI suggestions are for decision support only. Always confirm clinically.',
            'error': f'Gemini unavailable: {error}' if error else None,
        }

    def _log_diagnosis(self, patient_id, symptoms, result):
        try:
            from app.models import AIRecommendation
            from app import db
            top = result.get('differentials', [{}])[0] if result.get('differentials') else {}
            rec = AIRecommendation(
                patient_id=patient_id,
                recommendation_type='diagnosis_support',
                content=f"Symptoms: {symptoms[:100]}. Top differential: {top.get('diagnosis', 'N/A')}",
                confidence_score=0.8 if top.get('confidence') == 'High' else 0.5)
            db.session.add(rec)
            db.session.commit()
        except Exception:
            db.session.rollback()
