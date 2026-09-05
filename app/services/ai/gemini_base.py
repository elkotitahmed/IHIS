"""Shared Gemini LLM base for all AI features.

Provides a reusable `_call_gemini()` method and prompt-building helpers so
each clinical AI assistant can call Gemini without duplicating API logic.
Falls back gracefully when ``GEMINI_API_KEY`` is not set.
"""
import os
import re
import time
import json

import requests as _requests

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"


def gemini_available():
    return bool(os.getenv('GEMINI_API_KEY'))


def _collect_patient_context(patient):
    """Build a clinical context string for any LLM prompt."""
    from datetime import date
    age = 'N/A'
    if patient.date_of_birth:
        today = date.today()
        age = today.year - patient.date_of_birth.year - (
            (today.month, today.day)
            < (patient.date_of_birth.month, patient.date_of_birth.day))

    from app.models import (Prescription, LabOrder, LabResult, Diagnosis,
                            VitalSign, MedicalRecord)
    prescriptions = Prescription.query.filter_by(
        patient_id=patient.id, status='Active').all()
    med_lines = []
    for rx in prescriptions:
        for item in rx.items:
            med = item.medication
            if med:
                med_lines.append(
                    f"  - {med.generic_name} {item.dosage or ''} "
                    f"{item.frequency or ''} "
                    f"({'PRN' if getattr(item, 'as_needed', False) else 'Regular'})")

    lab_lines = []
    for order in LabOrder.query.filter_by(patient_id=patient.id).order_by(
            LabOrder.order_date.desc()).limit(20).all():
        r = order.result
        if r:
            tn = order.test.test_name if order.test else 'Lab'
            lab_lines.append(
                f"  - {tn}: {r.result_value} {order.test.unit if order.test else ''}"
                f" {'(ABNORMAL)' if r.is_abnormal else ''}")

    vitals = VitalSign.query.filter_by(
        patient_id=patient.id).order_by(
        VitalSign.recorded_at.desc()).first()
    vitals_text = 'No recent vitals'
    if vitals:
        parts = []
        if vitals.temperature: parts.append(f"Temp {vitals.temperature}C")
        if vitals.blood_pressure_systolic:
            parts.append(f"BP {vitals.blood_pressure_systolic}/{vitals.blood_pressure_diastolic}")
        if vitals.heart_rate: parts.append(f"HR {vitals.heart_rate}")
        if vitals.oxygen_saturation is not None:
            parts.append(f"SpO2 {vitals.oxygen_saturation}%")
        vitals_text = ', '.join(parts) if parts else 'No recent vitals'

    diagnoses = Diagnosis.query.filter_by(patient_id=patient.id).all()
    diag_lines = [f"  - {d.description} ({d.icd10_code or ''})" for d in diagnoses]

    ctx = f"""PATIENT INFORMATION:
- Age: {age} years
- Gender: {patient.gender or 'N/A'}
- Blood Type: {patient.blood_type or 'N/A'}
- Allergies: {patient.allergies or 'NKDA'}
- Chronic Diseases: {patient.chronic_diseases or 'None'}
- Known Diagnoses:
{chr(10).join(diag_lines) if diag_lines else '  - None documented'}

CURRENT MEDICATIONS:
{chr(10).join(med_lines) if med_lines else '  - None'}

RECENT LAB RESULTS:
{chr(10).join(lab_lines) if lab_lines else '  - None available'}

LATEST VITALS: {vitals_text}
"""
    return ctx


class GeminiBase:
    """Mixin providing Gemini API call capabilities to AI assistants."""

    def __init__(self, system_prompt="", model_name=None, temperature=0.4,
                 max_tokens=3000):
        self.api_key = os.getenv('GEMINI_API_KEY')
        self.model_name = model_name or os.getenv('AI_MODEL', DEFAULT_MODEL)
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens

    def available(self):
        return bool(self.api_key)

    def _call_gemini(self, user_message):
        if not self.api_key:
            raise RuntimeError('GEMINI_API_KEY not configured')
        full_prompt = f"{self.system_prompt}\n\n{user_message}"
        payload = {
            "contents": [{"role": "user",
                          "parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        url = (f"{BASE_URL}/{self.model_name}:generateContent"
               f"?key={self.api_key}")
        resp = _requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)

    def _call_gemini_json(self, user_message, temperature=0.2):
        """Call Gemini and parse the response as JSON.
        Handles markdown-fenced JSON blocks."""
        raw = self._call_gemini(user_message)
        raw = raw.strip()
        # Strip markdown fences if present
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```',
                       raw, re.DOTALL)
        if m:
            raw = m.group(1).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw_text": raw}

    def _log_call(self, feature_name, patient_id=None, extra=""):
        from app.routes.decorators import log_activity
        from flask_login import current_user
        uid = (current_user.id
               if current_user and current_user.is_authenticated
               else None)
        try:
            log_activity(f'AI_{feature_name}', 'patient',
                         patient_id, extra[:200] if extra else '')
        except Exception:
            pass
