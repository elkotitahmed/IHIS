# FEATURE GAP MATRIX — iHIS vs Open Source Reference Systems

Scoring key:
- **Value** (user impact): P0 = critical patient-safety/legal, P1 = high workflow value,
  P2 = useful, P3 = optional.
- **Effort**: S = <1h, M = half day, L = >1 day.
- **Status**: `gap` = absent, `partial` = exists, needs strengthening, `done` = satisfied.

Native reimplementation only (see `OPEN_SOURCE_ATTRIBUTIONS.md`; no external code copied).

## P0 — Critical patient safety / governance

| # | Capability | Reference evidence | iHIS today | Gap | Status | Plan / result |
|---|------------|--------------------|-----------|-----|--------|---------------|
| 1 | Structured allergies + safety strip on every clinical page | OpenMRS chart banner; FHIR AllergyIntolerance | `Allergy` model; Patient 360 panel | Allergy data not surfaced across ALL clinical pages | done | Reusable `PatientSafetyContext` service + `_patient_header.html` partial; wired into doctor patient pages, every AI patient page, pharmacy reconcile, nursing MAR, dentistry pages, Patient 360. |
| 2 | Independent double-check critical results & acknowledgement | OpenMRS chart; OpenEMR results | radiology critical-result notifications; pharmacist run check | No general lab-critical auto-flag or clinician acknowledgement log | done | `LabTestCatalog` critical thresholds (`critical_low`/`critical_high`) auto-flag panic values at entry and re-derive at verification; escalation to doctors (Notification + idempotent open `CRITICAL_LAB` alert) plus **Clinical Inbox** acknowledgement records (`ResultAcknowledgement`). |
| 3 | Medication interaction & therapeutic duplicate guard | OpenMRS dispensing; Bahmni | `DrugInteraction` service + pharmacist run check | Interactions run manually, per-prescription only | done | Existing. |
| 4 | Stock integrity / concurrent dispensing | OpenMRS stock mgmt | ledger-based atomic dispense | — | done | Existing (do not weaken). |
| 5 | Audit trail of clinical change | OpenMRS | `AuditLog` + Timeline | — | done | Existing. |

## P1 — High clinical workflow value (DELIVERY FOCUS OF THIS CYCLE)

| # | Capability | Reference evidence | iHIS today | Gap | Status | Plan / result |
|---|------------|--------------------|-----------|-----|--------|---------------|
| 6 | **Order sets** (reusable bundles) | Bahnmi order sets; OpenMRS orders | only AI smart orders | No reusable clinician-authored order sets, no apply-to-patient | done | `OrderSet`/`OrderSetItem` models + CRUD + apply flow (labs + imaging + drugs + referrals); newly created sets default to `is_active=False` (safety default). |
| 7 | **Clinical / encounter templates** | OpenEMR template-driven forms; OpenMRS forms | free-form EMR add | No template for SOAP/structure note | done | `ClinicianTemplate` model (title, specialty/role scope, SOAP sections JSON), pick on EMR compose, enabled/disabled toggle. |
| 8 | **Unified clinical inbox** | OpenMRS tasks; OpenEMR results | separate lists (alerts, follow-ups, pharmacy, radiology ack) | No single doctor landing surface to ack & act | done | `clinical.inbox` route: unreviewed lab results, open alerts, pending referrals, interventions, pending tasks, pending signature; actions link into existing flows. |
| 9 | **Result acknowledgement + critical escalation** | OpenEMR | clinical + radiology ack | No general lab ack log; escalation chain missing | done | `ResultAcknowledgement` model (idempotent; kind `CRITICAL`/`REVIEW`); inbox ack action; critical labs auto-escalate to doctors via Notification + open `CRITICAL_LAB` alert (see #2). |
| 10 | **Reminder / recall engine** | OpenEMR recall board; OpenMRS reminders | follow-ups scheduled manually; no due-engine | No proactive overdue/due surface | done | `ClinicalReminder` model + `app/services/reminders.py` computing due/overdue (idempotent dedupe, auto-close), ack + doctor/admin surfaces. |
| 11 | Smart patient header/banner reusable | OpenMRS patient header; FHIR PatientBanner | header in patient_360 only | Not reusable/visible on other clinical pages | done | `_patient_header.html` partial rendered by `patient_safety_context` across doctor, AI, pharmacy, nursing and dentistry pages (see #1). |
| 12 | Clinical summary (verified) | OpenMRS chart review; Bahnmi discharge summary | AI-generated narrative summary exists | No structured verified summary page | done | `clinical.summary` route building from real entities (problems, allergies, meds, labs, imaging, admissions, immunizations, care team, open alerts) plus printable view (`@media print`, print action). |

## P2 — Operational efficiency

| # | Capability | Reference evidence | iHIS today | Gap | Status | Plan / result |
|---|------------|--------------------|-----------|-----|--------|---------------|
| 13 | Lab worklist (status/specimen columns) | Bahnmi LIS worklist | lab worklist exists | no specimen/accession focus | done | Worklist now shows accession, specimen type + collection time, specimen-status badge; status / priority / critical-only filters plus order/accession search. |
| 14 | Expiry / low-stock / batch badging | OpenMRS stock mgmt | expiry UI exists | no near-expiry badges on lists | done | Priority-staged badges (Expired / Low Stock / Expires-soon with days left) with corrected precedence (expired never masked by low stock), expired-row highlight, filter tabs on the inventory list. |
| 15 | Partial dispensing visibility | OpenMRS dispensing | full dispense | per-item remaining not shown in queue | done | Partially dispensed prescriptions float to the queue top; per-rx progress bar (dispensed/total) + per-item remaining markers in queue and detail; cancelled/dispensed excluded. |
| 16 | In-patient dashboard widget | Bahnmi in-patient dashboard | ward module exists | no aggregated in-patient board | done | `admissions/dashboard` now adds a per-ward **census** (occupied/total/available + occupancy bar, full/houseful highlight) and a **Needs Attention** widget (overstay beyond `expected_discharge` and open HIGH/CRITICAL alerts on admitted patients) next to the existing KPIs; discharged patients drop out and their bed frees. |
| 17 | Scheduling recall board | OpenEMR | appointment module | no recall board | done | `clinical.recall_board` (`/clinical/recall-board`, REMINDER_VIEW) buckets scheduled follow-ups + open reminders into Overdue / Due Today / Upcoming with reminders auto-materialised by the recall engine (idempotent, see #10); one-click `Done` ack posts to the existing ack action. Scan now never auto-closes manual reminders (no source link). |

## P3 — Optional / UX polish

| # | Capability | Reference evidence | iHIS today | Gap | Status | Plan / result |
|---|------------|--------------------|-----------|-----|--------|---------------|
| 18 | FHIR resource view | fhir-ui tables | JSON API endpoints | no resource views | done | Read-only FHIR R4 feed (`/fhir`, `application/fhir+json`): `Patient` resource, per-patient `$everything` searchset Bundle (Observation + DiagnosticReport per lab, Condition from diagnoses/problems, AllergyIntolerance, Immunization, MedicationRequest, Encounter from admissions/appointments), patient index, plus an in-app browser view linked from patient 360. Access-gated: `TIMELINE_VIEW` + patient need-to-know (403 otherwise), never writes. |
| 19 | Form-designer DSL | OpenEMR/Bahmni forms | native forms | no dynamic forms | gap | Deferred; native forms preferred. |
| 20 | Observation density (ref ranges inline) | fhir-ui | lab tables | minor | done | Reference range shown next to every lab value: doctor lab-results table (with critical/abnormal flags), patient portal, lab worklist, clinical summary, patient overview, patient 360 and the result-entry page (which already shows range + critical thresholds). Doctor lab-results page corrected to read real `LabOrder`/`LabResult` fields. |
| 21 | Micro-frontends / workspaces | OpenMRS/Bahmni | server-rendered | — | rejected | Intentional (PythonAnywhere-compatible, simpler security model). |

## Why these choices
Prioritization follows the master prompt: **patient safety > clinical workflow >
operational efficiency > interoperability > UX > visibility > auditability**.
P0/P1 items above are patient-safety and clinical-workflow driven; operational items
are P2; MFE and form-designer are explicitly out of scope for a Flask/PythonAnywhere
native system.

Status roll-up (2026-09-05): **done 19, partial 0, gap 1 (rejected 1).**