# OPEN SOURCE BENCHMARK — iHIS vs OpenMRS / Bahmni / OpenEMR / FHIR UI

> Purpose: study mature open-source healthcare systems as **reference systems** only.
> iHIS is never cloned or replaced — patterns found here are **reimplemented natively**
> in Flask/Jinja/SQLAlchemy. No external source code is copied (see
> `OPEN_SOURCE_ATTRIBUTIONS.md`).

Research conducted 2026-09-05. Repositories inspected via public docs/README/wiki:
`openmrs/openmrs-esm-patient-chart`, Bahmni docs/wiki, `openmrs/openmrs-esm-dispensing-app`
(concept), `openmrs/openmrs-esm-stock-management` (concept), `openemr/openemr`,
`healthintellect/fhir-ui`. Items that could not be independently verified are marked
`NOT_VERIFIED`.

---

## Reference cards

| Reference | License (as published) | Reuse decision |
|-----------|------------------------|----------------|
| OpenMRS core + ESM frontends | MPL-2.0 (core); several ESM repos display "Other" | No code copied — patterns reimplemented |
| OpenMRS dispensing / stock ESM apps | MPL-2.0 heritage | No code copied — workflow patterns studied |
| Bahmni (bahmni-frontend, docs) | Mixed (EPL/ECL heritage; new frontend lists "Other"/Apache) — NOT_VERIFIED | No code copied |
| OpenEMR | GPL-3.0 (copyleft) | No code copied; used only as a *functional* roadmap |
| fhir-ui | MIT | No code copied; card/table component *concepts* reimplemented in Jinja |

---

## How to read this file

Columns: **Reference → Feature → Why it is good → How iHIS does it today → Gap →
Priority (P0/P1/P2/P3) → Native reimplementation plan → License note**.

---

## 1. OpenMRS patient chart

| Column | Value |
|--------|-------|
| Architecture | SPA micro-frontend: left **navigation menu**, persistent **patient header/banner**, **chart review** area of configurable **dashboards/widgets**, **workspace** side panel, side menu for notifications |
| Why it is good | One persistent patient context; clinicians keep the patient banner and alert/safety info visible while navigating between data-entry and review |
| How iHIS does it | Server-rendered `clinical/patient_360.html` with a rich header, care-strip of latest vitals, and per-panel cards. Workspaces don't exist (server round-trip model). |
| Gap | Header is per-template (not a reusable component); no configurable dashboard grid; no side-panel data entry |
| Priority | P1 (header reuse + banner) |
| Plan | Extract a **reusable patient header partial** (`_patient_header.html`) used by Patient 360 and clinical-facing pages (ALLERGIES, active problems, critical alerts, active meds from real data). |
| License note | MPL-2.0 — not compatible with a copy-verbatim approach without licensing iHIS accordingly; patterns only. |

### OpenMRS patient-banner facts (inspected README)
- Always at top: name, age, birthdate, gender, preferred identifier (MRN).
- "More" expands demographics.
- Uninvasive notifications (toasts) appear in the header area after form submissions.
- **iHIS equivalent**: `patient_360` header already shows name/MRN/age/gender/DOB/blood/phone/admitted (native). Toast system exists (`base.html`).

### OpenMRS allergies / conditions / problems / flags
- Dedicated **widgets** for allergies (verified + active), problems/conditions, and safety "flags".
- **iHIS equivalent**: structured `Allergy`, `Problem`, `ClinicalAlert` models + Patient 360 panels (`clinical.py` routes). GAP: allergy "verification status" and a **safety strip** that surfaces open alerts across every clinical page → P1.

### OpenMRS tests/orders/medications/immunizations
- Chronological widgets with status chips; orders have lifecycle (active/on-hold/stopped) and self-expire support.
- **iHIS equivalent**: `LabResult`, `RadiologyReport`, `PrescriptionItem.status`, `ImmunizationRecord` exist; orders show uid + status in lists.
- GAP: **order sets** (reusable bundles) and **clinical templates** (accelerated documentation) — P1 (see `FEATURE_GAP_MATRIX.md`).

### OpenMRS task list / patient reminders
- Patient-scoped task list widget; appointments/reminders surfaces.
- **iHIS equivalent**: global `Task` engine (tasks service), `FollowUp`, `Notification`, `ClinicalAlert`.
- GAP: a **clinical reminder engine** (overdue follow-ups, immunization/ monitoring due, review due) — P1.

---

## 2. OpenMRS dispensing (concept: `openmrs-esm-dispensing-app`)

| Column | Value |
|--------|-------|
| Architecture | Dedicated pharmacy "dispensing" surface driven by a prescription queue |
| Why it is good | Pharmacist sees an explicit queue, works each prescription against stock, records partial/full dispensing, and the medication history updates everywhere |
| How iHIS does it | `pharmacy.py`: prescription list, review, interaction check, dispense against `PharmacyInventory` with atomic stock decrement + ledger (`StockTransaction`), reconciliation + interventions |
| Gap | Partial dispensing vs. full quit; batch/expiry-aware FEFO picking is manual |
| Priority | P2 |
| Plan | Keep robust stock safeguards; clearly show per-item status (Active/Dispensed/Held) and remaining quantity in the pharmacy queue; add near-expiry + low-stock badges to inventory (`stock` module). |
| License note | Study only. |

---

## 3. OpenMRS stock management (concept)

| Column | Value |
|--------|-------|
| Architecture | Stock ops (receipt / issue / adjustment / return), batch + expiry tracking, stock balance, reports, transaction history |
| Why it is good | Traceable movement, FEFO expiry discipline, no silent shrinkage |
| How iHIS does it | `PharmacyInventory` + `StockTransaction` (ledger), concurrency-safe atomic dispense, inventory list, `medication expiry` UI, reports |
| Gap | No first-class **batch/lot** entity; adjustments/returns have no dedicated UI; no dedicated stock-movement report page |
| Priority | P2 |
| Plan | Add **expiry/near-expiry and low-stock** badges + a **stock movement report** page reusing the existing ledger (no schema change needed). No weakening of concurrency protections. |
| License note | Study only. |

---

## 4. Bahmni (workflow/lab/in-patient)

| Column | Value |
|--------|-------|
| Architecture | OpenMRS distro + Odoo ERP/PACS/LIS; AngularJS→React MFEs; patient dashboard of configurable widgets; in-patient dashboard + ward management |
| Why it is good | Feels like ONE hospital platform: every department shares the patient context, order sets, lab/radiology order integration, clinical forms, discharge summaries |
| How iHIS does it | Single Flask app, unified Patient 360, care team/MDT, orders → tasks routing, discharge fields in admissions, reception queue |
| Gap | No form-designer; ward/bed module exists but no in-patient dashboard widget; lab worklist lacks specimen/accession tracking |
| Priority | P2 (in-patient dashboard), P2 (lab worklist improvements) |
| Plan | Add a **Lab worklist** improvement (status filters + specimen accessible columns) natively. In-patient dashboard deferred (P3) unless ward data presence justifies it. |
| License note | Bahmni docs CC BY-SA 4.0; code EPL/ECL/other mix — study only. |

---

## 5. OpenEMR (functional benchmark)

| Column | Value |
|--------|-------|
| Architecture | Classic PHP LAMP monolith + ONC-certified FHIR API + patient portal |
| Why it is good | Mature practice management: scheduling with **recall/reminder board**, encounter + **template-driven forms**, encounter flows, e-prescribing, billing (ASC X12 5010), patient portal with secure messaging/payments |
| How iHIS does it | Scheduling + reception queue; SOAP AI notes; billing module + payments idempotency; patient portal (appointments, docs, messages, notifications, prescriptions, results); structured clinical models |
| Gap | No **reminders/recall board**; clinical note templates; results acknowledgement workflow |
| Priority | P1 (reminders), P1 (templates), P1 (result acknowledgement) |
| Plan | Implemented natively: **Order Sets**, **Clinical Templates**, **Clinical Inbox** (acknowledge critical/unreviewed results + alerts), **Reminder engine**. |
| License note | GPL-3.0 —— **copyleft**: no OpenEMR code enters iHIS. Functional/roadmap influence only. |

---

## 6. FHIR UI (React presentation components)

| Column | Value |
|--------|-------|
| Architecture | Material-UI React components: PatientCard/Table/Detail/Banner, Observation/ Condition/Allergy/Medication tables and detail panels |
| Why it is good | Information-dense clinical tables (id, DOB, gender, phone, org), patient banner with core demographics, observation detail rows |
| How iHIS does it | Native Jinja `table-ihs`, card-premium panels, Patient 360 header |
| Gap | Minor — more dense "observation table" rows in lab results (reference ranges visible inline) |
| Priority | P3 |
| Plan | Adopt density/coding conventions into existing tables (column standardization); no dependency added. |
| License note | MIT — permissive, but no code copied. |

---

## Consolidated pattern banks worth reimplementing (P1)

1. Patient banner/safety strip — persistent across clinical pages.
2. Order sets (labs + imaging + medications + referrals) with clinician confirmation.
3. Clinical/encounter templates for faster charting.
4. Doctor/Clinician **inbox** (results + alerts + referrals + interventions + signatures + tasks) with acknowledgement.
5. **Reminder engine** (follow-up / immunization / monitoring / review due; anti-duplicate).
6. Clinical summary (verified data, distinct from AI-generated narrative).
7. Result acknowledgement + escalation for critical values.

## Deferred / rejected (see `OPEN_SOURCE_VALUE_SCORE.md`)

- Micro-frontend (MFE) architecture, React/Carbon, workspace side-panels — rejected (PythonAnywhere-compatible server-rendered Flask intentionally).
- Form-designer DSL — deferred (P3); hard-coded native forms already exist.
- Full FHIR server — deferred (Phase 31: serializers/mappers foundation only).
- PACS/LIS/ERP integrations (Bahmni/OpenEMR stack) — rejected; not appropriate for iHIS scope.