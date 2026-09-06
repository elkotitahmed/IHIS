# iHIS AI Clinical Copilot

How AI is built into the physician and patient experience, and the rules it
obeys. Updated 2026-09-06.

## Principles

- **Deterministic clinical rules are authoritative.** Allergy, interaction,
  duplicate-therapy, critical-value and critical-finding checks come from the
  rule engine first. AI may explain, summarise, prioritise and draft; it can
  never override, hide or reorder a deterministic alert.
- **One entry point.** The ✦ AI Copilot button in the header opens a
  contextual drawer. Dashboards carry no AI cards, no chatbot and no
  auto-generated content.
- **Explicit and human-verified.** Every action is a click. Output is
  labelled `VERIFIED CLINICAL DATA` (from the chart) or `✦ AI ASSISTED` /
  `AI DRAFT` / `AI ANALYSIS`. Nothing is ever labelled "AI verified".
- **Nothing writes to the chart.** AI never creates or modifies a diagnosis,
  prescription, allergy, medication, note signature, lab or radiology
  finalisation or discharge. "Insert into note" pastes a draft marked
  `[AI DRAFT — review before signing]` into the textarea the physician is
  editing; saving and signing remain manual.
- **Free-tier economics.** Local first → cache → rules → Gemini only when it
  adds value. No call per keystroke; dashboards never call the provider.
- **Privacy.** Prompts carry clinical facts only (no name, MRN, phone,
  address). Patient AI can only read the logged-in patient's own released
  data; caches are keyed per patient and per content fingerprint.
- **Safety.** Patient/user text is wrapped as data between `<<<DATA>>>`
  markers under a system guard; instruction-like text is flagged in the
  audit trail; HTML/script is stripped from input and output; AI output is
  rendered as text.

## Platform (`app/services/ai/platform.py`)

| Concern | Mechanism |
|---------|-----------|
| Budget | `AI_ENABLED`, `AI_MAX_REQUESTS_PER_MINUTE` (10), `AI_MAX_REQUESTS_PER_DAY` (200), `AI_AUTOCOMPLETE_ENABLED`, `AI_HEAVY_FEATURES_ENABLED`; a provider 429 starts an `AI_COOLDOWN_AFTER_429_MINUTES` cool-down |
| Status | `READY` / `LIMITED` / `LIMIT_REACHED` / `UNAVAILABLE`, shown as a subtle pill; local tools always remain available |
| Cache | `ai_cache_entries`, key = SHA-256(feature, patient, content fingerprint); TTL `AI_CACHE_TTL_MINUTES`; changed source data misses automatically; SuperAdmin can clear |
| Audit | `ai_usage_logs`: user, role, feature, patient reference, provider, status, latency, cache hit, accepted/rejected, 429 events. Prompts and outputs are never stored |
| Hooks | `GeminiBase._call_gemini` calls the platform before/after every HTTP call, so legacy AI features respect the same budget and audit |

## Physician Copilot (`/ai/copilot/*`)

Groups and actions (role-filtered): **PATIENT** (smart summary, encounter,
problems, medications, allergies, timeline, pre-visit summary, inpatient
daily summary), **DIAGNOSTICS** (labs, trends, radiology, important results),
**REASONING** (differential considerations, red flags, questions,
investigations — "AI-Assisted Differential Support", never "diagnosis"),
**DOCUMENTATION** (HPI, SOAP structure, assessment, plan, encounter summary,
follow-up, post-visit summary and instructions, discharge draft, referral
summary), **SAFETY** (medication, allergy, interaction, reconciliation
review), **PREDICTIVE AI** (Dermatology, Radiology, Dentistry with honest
`AVAILABLE` / `COMING SOON` status), **PATIENT COMMUNICATION** (plain-language
summary, education, term explanation).

Each action returns verified chart sections first and, if the platform
allows, an AI section with a thumbs-up/down that is stored in the audit.

Other physician helpers:

- **Smart autocomplete** (`data-ac="hpi|exam|assessment|plan|diagnosis|referral|discharge|nursing|physio|dental"`):
  local phrase banks instantly (120 ms debounce), AI continuation only after
  1.2 s idle on ≥12 characters, cancellable, cached. Keys: ↓/↑ select, Enter
  confirm, Tab accept, Esc dismiss.
- **Smart diagnosis entry** (`data-dx-lookup`): local ICD-10 terminology,
  then the clinician's recent and favourite diagnoses; ✦ AI suggestions are
  optional and the physician selects explicitly.
- **Medication intelligence**: choosing a medication on the prescription
  form immediately shows rule-based allergy (including class cross-reactivity),
  interaction, duplicate and contraindication findings; AI review is a
  separate button.
- **Result review**: "Analyze with AI" on a lab result or radiology report
  (nothing is analysed on page load).
- **Smart Inbox** tabs: Critical, Results, Referrals, Pharmacy Interventions,
  Tasks, Messages, plus "What needs my attention first?" — a deterministic
  ranking the AI may narrate but not reorder.

## Radiology critical-finding engine (`app/services/radiology_critical.py`)

Report entered or signed → rules (authoritative, negation-aware) + local
classifier → `ClinicalAlert` (`ai_assisted`, `confidence`, `rationale`,
`assigned_to` = ordering physician) → URGENT task → notifications to the
physician and care team → red banner on every page → acknowledge →
in progress → resolve with documented action (required for HIGH/CRITICAL) or
dismiss with a mandatory reason → audit. Unacknowledged alerts escalate to
supervisors after `ALERT_ESCALATION_CRITICAL_MINUTES` (30) /
`ALERT_ESCALATION_HIGH_MINUTES` (120) via the preventive sweep. The
radiologist's report is never modified.

## Patient AI (`/ai/patient/*`)

Explain a released result, explain a medication, prepare for an appointment
(static checklist, no AI call), explain a term (glossary first), summarise
the record, prepare questions. Own record only; cross-patient access fails
with 404; unreleased results are not explained; every answer ends with when
to contact the physician.

## Configuration

```
AI_ENABLED=1
AI_MAX_REQUESTS_PER_MINUTE=10
AI_MAX_REQUESTS_PER_DAY=200
AI_AUTOCOMPLETE_ENABLED=1
AI_HEAVY_FEATURES_ENABLED=1
AI_CACHE_TTL_MINUTES=720
AI_COOLDOWN_AFTER_429_MINUTES=10
ALERT_ESCALATION_CRITICAL_MINUTES=30
ALERT_ESCALATION_HIGH_MINUTES=120
```

## Tests

`tests/test_ai_platform.py`, `tests/test_copilot.py`,
`tests/test_radiology_critical_alerts.py`, `tests/test_patient_ai.py`,
`tests/test_experience.py` — Gemini is mocked everywhere; the suite never
spends quota.
