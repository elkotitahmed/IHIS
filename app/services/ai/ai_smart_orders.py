"""AI Smart Order Sets.

Given a diagnosis or clinical presentation, suggests a comprehensive set of
laboratory tests, imaging studies, and prescriptions appropriate for the
condition — following evidence-based clinical pathways.
"""
from app import db
from app.models import (Patient, Diagnosis, LabOrder, Prescription,
                        RadiologyOrder, Medication)
from app.services.ai.gemini_base import GeminiBase, _collect_patient_context

SYSTEM_PROMPT = (
    "You are a clinical decision support system specializing in order set "
    "recommendations. Based on the patient's diagnosis or clinical presentation, "
    "suggest a comprehensive, evidence-based order set. Follow established "
    "clinical pathways (e.g., ATSI, UpToDate, local protocols). For each order, "
    "provide: the test/medication name, clinical rationale, priority level "
    "(STAT/Urgent/Routine), and any special instructions. Consider the patient's "
    "age, weight, renal function, allergies, and current medications to avoid "
    "duplications and interactions."
)

OUTPUT_FORMAT = (
    "Format your response as JSON:\n"
    '{"lab_orders": [{"test": "...", "rationale": "...", "priority": "STAT|Urgent|Routine", '
    '"fasting_required": true/false, "special_instructions": "..."}], '
    '"imaging_orders": [{"study": "...", "rationale": "...", "priority": "STAT|Urgent|Routine"}], '
    '"prescriptions": [{"medication": "...", "dosage": "...", "route": "...", '
    '"frequency": "...", "duration": "...", "rationale": "...", '
    '"contraindication_check": "..."}], '
    '"non_pharmacologic": ["..."], '
    '"follow_up_schedule": ["..."], '
    '"clinical_pathway": "...", '
    '"disclaimer": "Order set is advisory. All orders require physician verification."}'
)


class AISmartOrders(GeminiBase):
    """LLM-powered smart order set generator."""

    def __init__(self):
        super().__init__(system_prompt=SYSTEM_PROMPT,
                         temperature=0.3, max_tokens=5000)

    def generate_order_set(self, patient_id, diagnosis_text=""):
        if not self.available():
            return {'available': False,
                    'error': 'Smart order sets require Gemini API key.',
                    'order_set': None}

        patient = db.session.get(Patient, patient_id)
        if not patient:
            return {'error': 'Patient not found'}

        ctx = _collect_patient_context(patient)

        # Get existing diagnoses
        diagnoses = Diagnosis.query.filter_by(patient_id=patient_id).all()
        diag_text = '\n'.join(
            f"  - {d.description} ({d.icd10_code or ''})"
            for d in diagnoses) if diagnoses else '  - No documented diagnoses'

        prompt = (
            f"{ctx}\n\n"
            f"DOCUMENTED DIAGNOSES:\n{diag_text}\n\n"
            f"CLINICAL PRESENTATION / TARGET DIAGNOSIS:\n"
            f"  {diagnosis_text or 'Based on documented diagnoses'}\n\n"
            f"Generate a comprehensive order set for this patient.\n"
            f"{OUTPUT_FORMAT}"
        )

        try:
            result = self._call_gemini_json(prompt)
            if 'raw_text' in result:
                return {'available': True, 'raw': result['raw_text'],
                        'source': 'gemini', 'order_set': None}
            result['available'] = True
            result['source'] = 'gemini'
            self._log_order_set(patient_id, diagnosis_text)
            return result
        except Exception as e:
            return {'available': True, 'error': str(e), 'order_set': None}

    def _log_order_set(self, patient_id, diagnosis):
        try:
            from app.models import AIRecommendation
            rec = AIRecommendation(
                patient_id=patient_id,
                recommendation_type='smart_orders',
                content=f'Order set generated for: {diagnosis[:100] if diagnosis else "documented diagnoses"}',
                confidence_score=0.85)
            db.session.add(rec)
            db.session.commit()
        except Exception:
            db.session.rollback()
