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
# "gemini-flash-latest" is the provider alias that always resolves to the
# current free-tier flash model (pinned ids such as gemini-2.5-flash have been
# retired for new keys). Override per deployment with AI_MODEL.
DEFAULT_MODEL = "gemini-flash-latest"
# Tried once when the primary model answers 503 (overloaded) twice.
FALLBACK_MODEL = "gemini-flash-lite-latest"
RETRY_DELAY_SECONDS = 1.5
# (connect, read) timeouts: fail fast on an unreachable provider, allow a
# long generation once connected.
REQUEST_TIMEOUT = (10, 90)


class AIServiceError(RuntimeError):
    """Provider failure with a message that is safe to show to a clinician
    (never contains the request URL, the API key, or provider internals)."""


def _safe_provider_error(exc):
    """Map transport / HTTP errors to a short, key-free description."""
    status = getattr(getattr(exc, 'response', None), 'status_code', None)
    if status == 429:
        return 'The AI provider is rate-limiting requests; try again shortly.'
    if status is not None and status >= 500:
        return 'The AI provider is temporarily unavailable.'
    if status in (401, 403):
        return 'The AI provider rejected the configured credentials.'
    if status == 404:
        return 'The configured AI model was not found.'
    name = type(exc).__name__
    if 'Timeout' in name:
        return 'The AI provider did not respond in time.'
    if 'Connection' in name:
        return 'The AI provider could not be reached.'
    return 'The AI provider returned an unexpected response.'


def gemini_available():
    return bool(os.getenv('GEMINI_API_KEY'))


def provider_available():
    """Any language-model provider: Gemini (plan A) or Groq (plan B)."""
    from app.services.ai.groq_client import groq_available
    return gemini_available() or groq_available()


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
        from app.services.ai.groq_client import groq_available
        return bool(self.api_key) or groq_available()

    def _call_groq(self, user_message, feature, started, first_error=None):
        """Plan B: same prompt through Groq. Records its own usage row."""
        from app.services.ai import platform
        from app.services.ai import groq_client
        try:
            text = groq_client.chat(self.system_prompt, user_message,
                                    temperature=self.temperature, max_tokens=self.max_tokens)
        except Exception as exc:  # noqa: BLE001 - requests / ValueError / RuntimeError
            message = _safe_provider_error(exc) if isinstance(exc, _requests.RequestException) \
                else 'The backup AI provider returned an unexpected response.'
            self.last_usage_id = platform.after_provider_call(
                feature, ok=False, http_status=getattr(getattr(exc, 'response', None), 'status_code', None),
                latency_ms=int((time.monotonic() - started) * 1000),
                patient_id=getattr(self, 'patient_id', None),
                detail=(f'{first_error} | groq: {message}' if first_error else message), provider='groq')
            raise AIServiceError(first_error or message) from None
        self.last_provider = 'groq'
        self.last_usage_id = platform.after_provider_call(
            feature, ok=True, http_status=200, latency_ms=int((time.monotonic() - started) * 1000),
            patient_id=getattr(self, 'patient_id', None),
            detail=('fallback after gemini: ' + first_error) if first_error else 'gemini not configured',
            provider='groq')
        return text

    def _call_gemini(self, user_message):
        from app.services.ai.groq_client import groq_available
        if not self.api_key and not groq_available():
            raise RuntimeError('GEMINI_API_KEY not configured')
        self.last_provider = 'gemini'
        full_prompt = f"{self.system_prompt}\n\n{user_message}"
        payload = {
            "contents": [{"role": "user",
                          "parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        # The key travels in a header, never in the URL, so it can never be
        # echoed back through an exception message, a proxy log or a referrer.
        url = f"{BASE_URL}/{self.model_name}:generateContent"
        # Budget / cool-down / audit go through the shared AI platform so
        # every feature (legacy or new) respects the free-tier limits.
        from app.services.ai import platform
        feature = getattr(self, 'feature', None) or type(self).__name__
        started = time.monotonic()
        try:
            platform.before_provider_call(feature, heavy=getattr(self, 'heavy', False),
                                          autocomplete=getattr(self, 'autocomplete', False))
        except platform.AIBudgetExceeded as exc:
            if groq_available():
                # Gemini is in cool-down / over budget: plan B takes the call.
                return self._call_groq(user_message, feature, started, first_error=str(exc))
            raise AIServiceError(str(exc)) from None
        if not self.api_key:
            return self._call_groq(user_message, feature, started)
        fallback = os.getenv('AI_FALLBACK_MODEL', FALLBACK_MODEL)
        attempts = [(self.model_name, url)]
        attempts.append((self.model_name, url))                      # one retry on 503
        if fallback and fallback != self.model_name:                 # then a lighter model once
            attempts.append((fallback, f"{BASE_URL}/{fallback}:generateContent"))
        resp = None
        last_exc = None
        for i, (model_used, attempt_url) in enumerate(attempts):
            try:
                resp = _requests.post(
                    attempt_url,
                    headers={"Content-Type": "application/json",
                             "x-goog-api-key": self.api_key},
                    json=payload, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                last_exc = None
                break
            except _requests.RequestException as exc:
                last_exc = exc
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if status == 503 and i < len(attempts) - 1:
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                break
        if last_exc is not None:
            exc = last_exc
            message = _safe_provider_error(exc)
            self.last_usage_id = platform.after_provider_call(
                feature, ok=False,
                http_status=getattr(getattr(exc, 'response', None), 'status_code', None),
                latency_ms=int((time.monotonic() - started) * 1000),
                patient_id=getattr(self, 'patient_id', None), detail=message)
            if groq_available():
                return self._call_groq(user_message, feature, time.monotonic(), first_error=message)
            raise AIServiceError(message) from None
        self.last_usage_id = platform.after_provider_call(
            feature, ok=True, http_status=resp.status_code,
            latency_ms=int((time.monotonic() - started) * 1000),
            patient_id=getattr(self, 'patient_id', None))
        try:
            data = resp.json()
            parts = data["candidates"][0]["content"]["parts"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise AIServiceError('The AI provider returned a malformed response.') from None
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
