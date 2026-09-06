"""Gemini-powered laboratory result interpretation.

Replaces the simple threshold-comparison stub with an LLM that provides
clinically meaningful interpretation of lab results, considers the
full patient context, and flags critical values.
"""
from datetime import date
from app import db
from app.models import Patient, LabOrder, LabResult, Diagnosis, VitalSign
from app.services.ai.gemini_base import GeminiBase, _collect_patient_context
from app.services.laboratory import evaluate_abnormality

SYSTEM_PROMPT = (
    "You are a clinical pathologist providing laboratory result interpretation. "
    "Analyze the lab result in the context of the patient's full clinical "
    "picture. Provide: (1) Clinical significance of the result, (2) Whether "
    "the result is within normal limits, (3) Potential diagnoses or conditions "
    "suggested by this result, (4) Recommended follow-up tests if any, "
    "(5) Critical value alerts if applicable. Always correlate with the "
    "patient's symptoms, other lab results, and clinical history. Use "
    "evidence-based interpretation guidelines."
)

OUTPUT_FORMAT = (
    "Format your response as JSON:\n"
    '{"clinical_significance": "...", "abnormal": true/false, '
    '"severity": "Critical|Abnormal|Borderline|Normal", '
    '"possible_conditions": ["..."], "clinical_correlation": "...", '
    '"recommended_followup": ["..."], "critical_alerts": ["..."], '
    '"explanation_for_patient": "...", '
    '"disclaimer": "..."}'
)


class AILaboratoryInterpretation(GeminiBase):
    """LLM-powered lab result interpretation."""

    def __init__(self):
        super().__init__(system_prompt=SYSTEM_PROMPT,
                         temperature=0.3, max_tokens=3000)

    def interpret_result(self, order_id, use_ai=True):
        """``use_ai=False`` returns the laboratory facts + reference-range flag only."""
        order = db.session.get(LabOrder, order_id)
        if not order or not order.result:
            return {'error': 'Lab order or result not found'}

        test = order.test
        result = order.result
        patient = order.patient

        # Always compute the basic abnormality flag
        abnormal = bool(result.is_abnormal)
        if not abnormal and test and test.normal_range:
            abnormal = evaluate_abnormality(result.result_value, test.normal_range)

        base_info = {
            'test': test.test_name if test else '-',
            'category': test.category if test else '-',
            'result': result.result_value,
            'unit': test.unit if test else '-',
            'normal_range': test.normal_range if test else '-',
            'abnormal': abnormal,
            'comment': result.result_notes,
        }

        if not use_ai or not self.available():
            return {**base_info,
                    'interpretation': (
                        'Above or below the reference range. Review clinically.'
                        if abnormal else 'Within the reference range.'),
                    'source': 'heuristic'}

        if patient is None:
            return {**base_info, 'interpretation': 'Patient record unavailable.',
                    'source': 'heuristic'}
        ctx = _collect_patient_context(patient)
        prompt = (
            f"{ctx}\n\n"
            f"LABORATORY RESULT TO INTERPRET:\n"
            f"  Test: {test.test_name if test else 'Unknown'}\n"
            f"  Category: {test.category if test else 'Unknown'}\n"
            f"  Result Value: {result.result_value} {test.unit if test else ''}\n"
            f"  Reference Range: {test.normal_range if test else 'Not provided'}\n"
            f"  Abnormal Flag: {'Yes' if abnormal else 'No'}\n"
            f"  Lab Notes: {result.result_notes or 'None'}\n\n"
            f"{OUTPUT_FORMAT}"
        )

        try:
            llm_result = self._call_gemini_json(prompt)
            if 'raw_text' in llm_result:
                return {**base_info, 'interpretation': llm_result['raw_text'],
                        'source': 'gemini', 'available': True}
            # Merge LLM interpretation with base info. The laboratory's own
            # verified facts (value, unit, abnormal flag) always win over the
            # model's output.
            merged = {**llm_result, **base_info, 'available': True, 'source': 'gemini'}
            merged.setdefault('interpretation',
                              llm_result.get('clinical_significance')
                              or llm_result.get('summary') or '')
            self._log_interpretation(order_id, patient.id, merged)
            return merged
        except Exception as e:  # noqa: BLE001
            from app.services.ai.gemini_base import AIServiceError
            reason = str(e) if isinstance(e, AIServiceError) else 'provider error'
            return {**base_info,
                    'interpretation': f'AI interpretation unavailable ({reason}). '
                                      'Manual review recommended.',
                    'source': 'error', 'available': True}

    def _log_interpretation(self, order_id, patient_id, result):
        try:
            from app.models import AIRecommendation
            rec = AIRecommendation(
                patient_id=patient_id,
                recommendation_type='lab_interpretation',
                content=f"Test: {result.get('test', 'N/A')}. "
                        f"Severity: {result.get('severity', 'N/A')}. "
                        f"{result.get('clinical_significance', '')[:150]}",
                confidence_score=0.85)
            db.session.add(rec)
            db.session.commit()
        except Exception:
            db.session.rollback()
