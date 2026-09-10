"""Gemini-powered patient risk prediction.

Replaces the additive-score heuristic with an LLM that synthesizes
the full clinical picture into a narrative risk assessment with
individualized risk factors and mitigation strategies.
"""
from datetime import date
from app import db
from app.models import (Patient, Diagnosis, VitalSign, LabResult, LabOrder,
                        Prescription, Admission, Medication)
from app.services.ai.gemini_base import GeminiBase, _collect_patient_context

SYSTEM_PROMPT = (
    "You are a clinical risk stratification specialist. Analyze the "
    "patient's complete clinical profile and provide a comprehensive "
    "risk assessment. Consider: demographics, comorbidities, medication "
    "burden, recent vital sign trends, laboratory abnormalities, "
    "admission history, and overall clinical trajectory. Assign an "
    "overall risk level (Critical/High/Moderate/Low) with individualized "
    "risk factors and actionable mitigation strategies. Consider "
    "readmission risk, adverse drug event risk, falls risk (if elderly), "
    "and clinical deterioration risk."
)

OUTPUT_FORMAT = (
    "Format your response as JSON:\n"
    '{"overall_risk": "Critical|High|Moderate|Low", "risk_score": 0-100, '
    '"risk_factors": [{"factor": "...", "impact": "High|Moderate|Low", "detail": "..."}], '
    '"mitigation_strategies": ["..."], '
    '"readmission_risk": "High|Moderate|Low", '
    '"adverse_drug_event_risk": "High|Moderate|Low", '
    '"falls_risk": "High|Moderate|Low|N/A", '
    '"clinical_trajectory": "Improving|Stable|Deteriorating", '
    '"recommended_interventions": ["..."], '
    '"narrative": "...", '
    '"disclaimer": "..."}'
)


class AIPatientRiskPrediction(GeminiBase):
    """LLM-powered patient risk prediction."""

    def __init__(self):
        super().__init__(system_prompt=SYSTEM_PROMPT,
                         temperature=0.3, max_tokens=4000)

    def predict_risk(self, patient_id, use_ai=True):
        """``use_ai=False`` returns the deterministic heuristic only (no provider call)."""
        patient = db.session.get(Patient, patient_id)
        if not patient:
            return {'error': 'Patient not found'}

        # Always compute the basic heuristic first as a fallback
        base_risk = self._basic_risk(patient_id)

        if not use_ai or not self.available():
            return {**base_risk, 'source': 'heuristic'}

        ctx = _collect_patient_context(patient)

        # Additional clinical context for risk assessment
        admissions = Admission.query.filter_by(
            patient_id=patient_id).order_by(
            Admission.admitted_at.desc()).limit(5).all()
        adm_text = '\n'.join(
            f"  - {a.admitted_at.strftime('%Y-%m-%d') if a.admitted_at else 'N/A'} "
            f"Reason: {a.reason or 'N/A'} Status: {a.status or 'N/A'}"
            for a in admissions) if admissions else '  - No prior admissions'

        medications = Prescription.query.filter_by(
            patient_id=patient_id, status='Active').all()
        med_count = sum(len(p.items) for p in medications)

        abnormal_labs = LabResult.query.join(LabOrder).filter(
            LabOrder.patient_id == patient_id,
            LabResult.is_abnormal.is_(True)).count()

        prompt = (
            f"{ctx}\n\n"
            f"ADMISSION HISTORY:\n{adm_text}\n\n"
            f"ACTIVE MEDICATION COUNT: {med_count}\n"
            f"ABNORMAL LAB COUNT: {abnormal_labs}\n\n"
            f"{OUTPUT_FORMAT}"
        )

        try:
            result = self._call_gemini_json(prompt)
            if 'raw_text' in result:
                return {**base_risk, 'narrative': result['raw_text'],
                        'source': 'gemini', 'available': True}
            result['available'] = True
            result['source'] = 'gemini'
            # Normalise onto the keys every template/consumer relies on.
            level = str(result.get('overall_risk') or result.get('level') or base_risk['level'])
            level = level.strip().title()
            if level not in ('Low', 'Moderate', 'High'):
                level = {'Medium': 'Moderate', 'Critical': 'High', 'Severe': 'High'}.get(level, base_risk['level'])
            result['level'] = level
            try:
                result['score'] = int(float(result.get('risk_score', base_risk['score'])))
            except (TypeError, ValueError):
                result['score'] = base_risk['score']
            factors = result.get('risk_factors') or []
            reasons = []
            for f in factors:
                if isinstance(f, dict):
                    reasons.append(str(f.get('factor') or f.get('detail') or ''))
                else:
                    reasons.append(str(f))
            result['reasons'] = [r for r in reasons if r] or base_risk['reasons']
            result.setdefault('patient', base_risk['patient'])
            result.setdefault('disclaimer', base_risk['disclaimer'])
            self._log_risk(patient_id, result)
            return result
        except Exception as e:  # noqa: BLE001
            from app.services.ai.gemini_base import AIServiceError
            return {**base_risk, 'source': 'heuristic',
                    'ai_error': str(e) if isinstance(e, AIServiceError) else 'provider error',
                    'available': True}

    def _basic_risk(self, patient_id):
        """Basic heuristic risk calculation as fallback."""
        patient = db.session.get(Patient, patient_id)
        score = 0
        reasons = []
        if patient.date_of_birth:
            age = (date.today() - patient.date_of_birth).days // 365
            if age >= 65:
                score += 2; reasons.append('Age 65+')
            elif age <= 2:
                score += 2; reasons.append('Age under 3')
        if patient.chronic_diseases:
            score += 2; reasons.append('Chronic disease present')
        if patient.allergies and not any(m in patient.allergies.lower() for m in ('nkda', 'no known', 'none')):
            score += 1; reasons.append('Documented allergies')
        abnormal = LabResult.query.join(LabOrder).filter(
            LabOrder.patient_id == patient_id,
            LabResult.is_abnormal.is_(True)).count()
        if abnormal:
            score += min(abnormal, 3)
            reasons.append(f'{abnormal} abnormal lab result(s)')
        vitals = VitalSign.query.filter_by(
            patient_id=patient_id).order_by(
            VitalSign.recorded_at.desc()).first()
        if vitals:
            if vitals.blood_pressure_systolic and vitals.blood_pressure_systolic >= 140:
                score += 1; reasons.append('Elevated systolic blood pressure')
            if vitals.heart_rate and vitals.heart_rate > 100:
                score += 1; reasons.append('Tachycardia')
        level = 'Low'
        if score >= 6: level = 'High'
        elif score >= 3: level = 'Moderate'
        return {'patient': patient.user.full_name if patient.user else '-',
                'score': score, 'level': level, 'reasons': reasons,
                'risk_factors': [{'factor': r, 'impact': 'Moderate', 'detail': r}
                                 for r in reasons],
                'narrative': 'Heuristic risk estimate (Gemini unavailable).',
                'disclaimer': 'Clinical risk estimate for triage; not a diagnosis.'}

    def _log_risk(self, patient_id, result):
        try:
            from app.models import AIRecommendation
            rec = AIRecommendation(
                patient_id=patient_id,
                recommendation_type='risk_prediction',
                content=f"Risk: {result.get('overall_risk', 'N/A')}. "
                        f"Narrative: {result.get('narrative', '')[:200]}",
                confidence_score=0.8)
            db.session.add(rec)
            db.session.commit()
        except Exception:
            db.session.rollback()
