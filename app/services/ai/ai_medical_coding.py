"""Gemini-powered ICD-10 medical coding assistant.

Replaces the 12-keyword dictionary with an LLM that reads free-text
clinical notes and suggests precise ICD-10-CM codes with explanations.
"""
from app import db
from app.models import Patient, Diagnosis
from app.services.ai.gemini_base import GeminiBase, _collect_patient_context

SYSTEM_PROMPT = (
    "You are an expert medical coder certified in ICD-10-CM. Analyze the "
    "clinical text provided and suggest appropriate ICD-10-CM codes. For "
    "each code, provide: the full ICD-10-CM code, code description, "
    "clinical reasoning for why this code applies, and whether the code "
    "requires additional specificity (laterality, episode of care, etc.). "
    "Prioritize the most specific codes available. Consider all documented "
    "conditions. Note: always verify codes with the medical record and "
    "clinical judgment — these are coding assistance suggestions only."
)

OUTPUT_FORMAT = (
    "Format your response as JSON:\n"
    '{"codes": [{"code": "A00.0", "description": "Cholera due to Vibrio cholerae 01, biovar cholerae", '
    '"reasoning": "...", "specificity_notes": "...", "confidence": "High|Moderate|Low"}], '
    '"missing_specificity": ["..."], '
    '"coding_notes": "...", '
    '"disclaimer": "..."}'
)


class AIMedicalCodingAssistant(GeminiBase):
    """LLM-powered ICD-10 coding assistant."""

    def __init__(self):
        super().__init__(system_prompt=SYSTEM_PROMPT,
                         temperature=0.2, max_tokens=3000)

    def suggest_code(self, text, patient_id=None):
        if not self.available():
            return self._fallback_heuristic(text)

        # Add patient context if available
        ctx = ""
        if patient_id:
            patient = db.session.get(Patient, patient_id)
            if patient:
                ctx = _collect_patient_context(patient)

        prompt = (
            f"{ctx}\n\n"
            f"Clinical text to code:\n  {text}\n\n"
            f"{OUTPUT_FORMAT}"
        )

        try:
            result = self._call_gemini_json(prompt)
            if 'raw_text' in result:
                return {'available': True, 'raw': result['raw_text'],
                        'source': 'gemini'}
            result['available'] = True
            result['source'] = 'gemini'
            self._log_coding(patient_id, text, result)
            return result
        except Exception as e:
            return {**self._fallback_heuristic(text),
                    'ai_error': str(e), 'available': True}

    def suggest_codes_from_diagnosis(self, patient_id):
        """Suggest ICD-10 codes for all diagnoses of a patient."""
        patient = db.session.get(Patient, patient_id)
        if not patient:
            return {'error': 'Patient not found'}

        diagnoses = Diagnosis.query.filter_by(patient_id=patient_id).all()
        if not diagnoses:
            return {'codes': [], 'note': 'No diagnoses documented for this patient.'}

        diag_text = '\n'.join(
            f"- {d.description} (existing code: {d.icd10_code or 'None'})"
            for d in diagnoses)

        if self.available():
            ctx = _collect_patient_context(patient)
            prompt = (
                f"{ctx}\n\n"
                f"Patient's documented diagnoses:\n{diag_text}\n\n"
                f"For each diagnosis, suggest the most specific ICD-10-CM code.\n"
                f"{OUTPUT_FORMAT}"
            )
            try:
                result = self._call_gemini_json(prompt)
                if 'raw_text' in result:
                    return {'available': True, 'raw': result['raw_text'],
                            'source': 'gemini'}
                result['available'] = True
                result['source'] = 'gemini'
                return result
            except Exception:
                pass

        return self._fallback_heuristic(diag_text)

    def _fallback_heuristic(self, text):
        ICD = {
            'hypertension': 'I10', 'diabetes': 'E11.9', 'pneumonia': 'J18.9',
            'asthma': 'J45.9', 'anemia': 'D64.9', 'gastritis': 'K29.7',
            'headache': 'R51', 'angina': 'I20.9', 'depression': 'F32.9',
            'arthritis': 'M13.9', 'respiratory infection': 'J06.9',
            'fracture': 'S52.9',
        }
        text = (text or '').lower()
        matches = {}
        for keyword, code in ICD.items():
            if keyword in text:
                matches[keyword] = code
        return {'matches': matches, 'source': 'heuristic',
                'note': 'Verify code specificity on chart review.',
                'codes': [{'code': c, 'description': k, 'reasoning': f'Keyword match: {k}',
                           'confidence': 'Moderate'} for k, c in matches.items()],
                'available': False}

    def _log_coding(self, patient_id, text, result):
        if not patient_id:
            return
        try:
            from app.models import AIRecommendation
            codes = result.get('codes', [])
            top = codes[0].get('code', 'N/A') if codes else 'N/A'
            rec = AIRecommendation(
                patient_id=patient_id,
                recommendation_type='medical_coding',
                content=f"Clinical text: {text[:100]}. Top code: {top}",
                confidence_score=0.85)
            db.session.add(rec)
            db.session.commit()
        except Exception:
            db.session.rollback()
