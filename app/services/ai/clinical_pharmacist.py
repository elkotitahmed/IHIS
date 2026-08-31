"""AI Clinical Pharmacy service — integrates the Gemini-powered clinical
pharmacist from the standalone "AI-Clinical-Pharmacist" project into iHIS.

Unlike the rule-based AI layer in ``ai_interfaces.py``, this assistant uses the
real Gemini API to run a comprehensive medication therapy review (MTR) and
returns sectioned clinical recommendations. If no ``GEMINI_API_KEY`` is
configured, :meth:`available` returns False and callers show a friendly notice
instead of crashing.
"""
import os
import re
import time

SYSTEM_PROMPT = (
    "You are a highly qualified Clinical Pharmacist AI Assistant specialized in "
    "medication therapy management and clinical decision support. Your role is to "
    "provide evidence-based pharmaceutical care recommendations. Your expertise "
    "includes: drug-drug interactions analysis; dose adjustments for renal/hepatic "
    "impairment; pregnancy and lactation safety; geriatric and pediatric "
    "considerations; adverse drug reactions; medication monitoring and laboratory "
    "value interpretation; therapeutic alternatives and optimization; patient "
    "education and counseling. Guidelines: base recommendations on current clinical "
    "guidelines (Micromedex, Lexicomp, UpToDate); consider patient-specific factors "
    "(age, renal, hepatic, comorbidities); flag serious drug-disease "
    "contraindications; suggest monitoring parameters and frequency; provide "
    "alternative medications when appropriate; be concise but comprehensive; use "
    "professional clinical language. Never make treatment decisions alone; assist "
    "the pharmacist/physician. Your recommendations assist healthcare "
    "professionals but do NOT replace clinical judgment. The reviewing "
    "pharmacist/physician has final authority on all clinical decisions."
)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.6-flash"  # overridable via AI_MODEL env var

_SECTION_HEADERS = [
    'Drug-Drug Interactions', 'Drug-Disease Contraindications', 'Duplicate Therapy',
    'Dose Adjustment', 'Renal Adjustment', 'Hepatic Adjustment',
    'Pregnancy Considerations', 'Lactation Considerations', 'Geriatric Considerations',
    'QT Prolongation', 'Potential Adverse Reactions', 'Monitoring',
    'Patient Counseling', 'Safer Alternatives', 'Clinical Summary', 'Action Plan',
]


def gemini_available():
    """True when a Gemini API key is present so the LLM feature can be offered."""
    return bool(os.getenv('GEMINI_API_KEY'))


class AIClinicalPharmacist:
    """Real LLM-backed medication review assistant (Gemini)."""

    def __init__(self, api_key=None, model_name=DEFAULT_MODEL):
        self.api_key = api_key or os.getenv('GEMINI_API_KEY')
        self.model_name = model_name or os.getenv('AI_MODEL', DEFAULT_MODEL)
        self.last_error = None

    def available(self):
        return bool(self.api_key)

    def _build_prompt(self, patient, prescriptions, lab_orders):
        """Assemble the medication-review prompt from iHIS model data."""
        now = time.localtime()
        birth = patient.date_of_birth
        age = 'N/A'
        if birth:
            from datetime import date
            age = date.today().year - birth.year - (
                (date.today().month, date.today().day)
                < (birth.month, birth.day))

        gender = patient.gender or 'N/A'
        mrn = patient.mrn or 'N/A'
        allergies = patient.allergies or 'NKDA (No Known Drug Allergies)'
        diagnoses = (patient.chronic_diseases or 'Not specified')

        # --- medications ---
        med_lines = []
        for rx in prescriptions:
            for item in rx.items:
                if item.medication_id:
                    med = item.medication
                    med_lines.append(
                        f"- {med.generic_name} "
                        f"{item.dosage or ''} {item.frequency or ''} "
                        f"{'(' + item.instructions + ')' if item.instructions else ''}")
        med_text = "\n".join(med_lines) if med_lines else "No current medications"

        # --- labs (latest of each completed order) ---
        lab_lines = []
        for order in lab_orders:
            result = getattr(order, 'result', None)
            if result:
                test_name = order.test.test_name if order.test else 'Lab'
                lab_lines.append(
                    f"- {test_name}: {result.result_value} "
                    f"{result.result_unit or ''}"
                    f"{' (ABNORMAL)' if result.is_abnormal else ''}")
        lab_lines = lab_lines or ['No recent lab results']

        prompt = f"""You are performing a comprehensive medication therapy review for a patient. Analyze the provided patient information and medications, then provide detailed clinical recommendations.

PATIENT INFORMATION:
- Name: {patient.user.full_name if patient.user else 'N/A'}
- Age: {age} years
- Gender: {gender}
- MRN: {mrn}

CURRENT MEDICATIONS:
{med_text}

ALLERGIES:
{allergies}

DIAGNOSES:
{diagnoses}

LABORATORY RESULTS:
{chr(10).join(lab_lines)}

Please provide a comprehensive medication review including:

1. **Drug-Drug Interactions**: Identify all potential interactions between medications
2. **Drug-Disease Contraindications**: Check for contraindications with patient's diagnoses
3. **Duplicate Therapy**: Identify redundant medications
4. **Dose Adjustment**: Check if any doses need adjustment based on renal/hepatic function
5. **Renal Adjustment**: Specific recommendations for renal-impaired patients
6. **Hepatic Adjustment**: Specific recommendations for hepatic-impaired patients
7. **Pregnancy Considerations**: If applicable, note pregnancy-related concerns
8. **Lactation Considerations**: If applicable, note lactation-related concerns
9. **Geriatric Considerations**: If patient is elderly (>=65 years), note age-specific concerns
10. **Pediatric Considerations**: If patient is pediatric (<18 years), note age-specific concerns
11. **QT Prolongation**: Check for drugs that prolong QT interval
12. **Potential Adverse Reactions**: List common and serious ADRs to monitor
13. **Monitoring Recommendations**: Specific parameters and frequency for monitoring
14. **Patient Counseling Points**: Key points for patient education
15. **Safer Alternatives**: Suggest safer medication alternatives when applicable

Format each section clearly with actionable recommendations. Prioritize serious interactions and contraindications."""
        return prompt.strip()

    def _call_gemini(self, user_message, temperature=0.5, max_tokens=3000):
        import requests
        if not self.api_key:
            raise RuntimeError('GEMINI_API_KEY not configured')
        full_prompt = f"{SYSTEM_PROMPT}\n\n[Patient Case Data]:\n{user_message}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
            "generationConfig": {"temperature": temperature,
                                 "maxOutputTokens": max_tokens},
        }
        url = f"{BASE_URL}/{self.model_name}:generateContent?key={self.api_key}"
        resp = requests.post(url, headers={"Content-Type": "application/json"},
                             json=payload, timeout=90)
        resp.raise_for_status()
        result = resp.json()
        candidate = result["candidates"][0]
        parts = candidate["content"]["parts"]
        return "".join(part.get("text", "") for part in parts)

    def _parse_sections(self, content):
        """Split the free-form LLM text into named sections."""
        sections = {}
        current = None
        buf = []
        for line in content.split('\n'):
            matched = None
            for h in _SECTION_HEADERS:
                if h.lower() in line.lower():
                    matched = h
                    break
            if matched:
                if current:
                    sections[current] = '\n'.join(buf).strip()
                current = matched
                buf = []
            else:
                buf.append(line)
        if current:
            sections[current] = '\n'.join(buf).strip()
        return sections

    def review(self, patient, prescriptions=None, lab_orders=None):
        """Run a full medication review and return a structured result dict."""
        from app.models import Prescription, LabOrder
        prescriptions = prescriptions if prescriptions is not None else \
            Prescription.query.filter_by(patient_id=patient.id).all()
        lab_orders = lab_orders if lab_orders is not None else \
            LabOrder.query.filter_by(patient_id=patient.id).order_by(
                LabOrder.order_date.desc()).all()

        if not self.api_key:
            return {'available': False, 'error': 'AI service not configured'}

        start = time.time()
        try:
            prompt = self._build_prompt(patient, prescriptions, lab_orders)
            content = self._call_gemini(prompt)
            sections = self._parse_sections(content)
            keys = list(sections.keys())
            return {
                'available': True,
                'sections': sections,
                'content': content.strip(),
                'order': keys,
                'duration_ms': int((time.time() - start) * 1000),
            }
        except Exception as e:
            self.last_error = str(e)
            return {'available': True, 'error': f'AI service error: {e}',
                    'sections': {}, 'content': '', 'order': []}
