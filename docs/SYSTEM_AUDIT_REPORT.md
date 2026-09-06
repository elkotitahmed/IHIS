# iHIS System Audit Report

Full-system takeover audit, repair and validation of the iHIS Flask platform.
Baseline tag `audit-baseline` (commit `938522b`, 2026-09-05). Work landed in
commits `461a0fe`, `5cda9ca`, `2fbd9eb` and the follow-up commit that ships
this report. Updated 2026-09-06.

## Method

1. Checkpoint: baseline suite (232 tests), migration head `8f3c0d1a2e9b`,
   276 routes, dev database backed up to `database/ihis.db.pre-audit-*`.
2. Inventory and forensic grep of routes, templates, services, models,
   permissions, migrations and tests.
3. Role-by-route white-page forensics: `scripts/smoke_all_routes.py` logs in
   as every demo role and requests every GET route (2,587 combinations),
   failing on any 500, empty body or redirect loop.
4. Per-page SQL profile: `scripts/profile_queries.py` counts statements on
   the hot pages for four roles and fails above budget.
5. Browser validation (Claude in-app browser) of the Command Center, Demo
   Center, System Health, Patient 360, every role workspace, RTL, dark mode
   and 375 px mobile.
6. Regression tests for every repaired defect; full suite after each pass.
7. Independent sub-agent audit of the services layer; HIGH findings fixed.

## Findings and repairs

### Access control and data boundaries
- Need-to-know rewritten in `app/access.py` for all 13 roles (department,
  care team, authorship, today's appointment, admission, orders, referrals by
  specialty keywords, bills for Cashier). `accessible_patient_ids` mirrors it
  so every filtered list yields openable pages.
- New role **RadiologyTechnician** (schedule/arrive/perform/upload/dose; cannot
  report or sign); Radiologist gained inbox/alerts/timeline/search;
  LabTechnician gained timeline/search; Doctor gained discharge.
- Global search crashed (500) when a query matched a patient document;
  doctor rows in search results built a link with a missing patient id. Fixed
  and regression-tested.
- 403 page rendered blank for logged-in users (error templates only filled
  the unauthenticated block). Fixed for all error pages.
- Locked statuses now include `Finalized`; amendments go through audited
  flows only. SuperAdmin visibility never bypasses locks.
- Role preview and patient-portal preview are explicit and labelled.

### Workflow integrity
- Shared creation path `app/services/clinical_orders.py` for lab orders,
  radiology orders, prescriptions and referrals: record → safety screen →
  task → timeline → notification. Doctor, API, order-set and care routes all
  use it (previously four divergent code paths).
- Referral lifecycle state machine with receiver/sender rules; tasks close and
  the referrer is notified on acceptance, completion or rejection.
- Lab: reorder creates a real accessioned order and cancels the old task;
  verify completes the task and notifies only the ordering clinicians;
  critical values escalate; a never-accepted order can now be cancelled.
- Radiology: performing a study creates the reporting task; amendments are
  versioned; signing notifies ordering clinicians; manual critical-finding
  flag; acknowledgement requires patient access.
- Pharmacy: expired batches are never dispensed; full dispense completes the
  task; interventions notify the prescriber directly; stock can never go
  negative; intervention form now reachable from the prescription page.
- Nursing: dashboard, patient list and MAR scoped; MAR guards for cancelled
  prescriptions and already-recorded doses; `Discontinued` outcome when a
  prescription is cancelled.
- Reception: check-in through the appointment state machine with queue
  numbers; cancel and reschedule routes; today-only queue; registration
  requires email, assigns an MRN, and checks duplicates first.
- Admissions: bed must belong to the ward; atomic bed claim; admission
  detail page; discharge requires a diagnosis, bills the room idempotently,
  closes tasks and can schedule a follow-up.
- Billing: bill and receipt numbers derived from primary keys (no
  collisions); completed services use verified/signed statuses; payment,
  bill and void events on the timeline.
- Legacy status spellings (`ORDERED`, `PERFORMED`, `FINALIZED`, `Completed`,
  `In Progress`, `No Show`) normalised by data migration `b7c2e9d41f05`;
  seeders fixed at source.
- Notification engine: unread-duplicate suppression, care-team and
  ordering-clinician helpers. Task engine: resource-scoped complete/cancel.

### Patient 360 and timeline
- `merged_timeline` synthesises events for every source record that predates
  the timeline engine (appointments, encounters, diagnoses, orders, results,
  prescriptions, dispensing, admissions, discharges, referrals, therapy,
  dental, bills, payments, follow-ups, immunizations, documents, vitals,
  nursing notes), de-duplicated against persisted events.
- Patient 360 shows cross-department panels (labs, imaging, prescriptions,
  encounters, referrals, tasks, appointments, billing balance, therapy,
  dental, nursing, MAR, admissions, care team) and inline allergy/problem
  editing for roles with the edit permission.

### AI platform
- Gemini base: API key sent in a header (never the URL), sanitized provider
  errors (`AIServiceError`), connect/read timeouts, no PHI names in prompts.
- All Gemini-backed services and the image models (fracture, tooth, skin)
  degrade gracefully; the skin-lesion page reported a missing model only
  because a stale system-Python server lacked `timm`; the app must run from
  the project venv (documented in the demo guide).
- Lab interpretation, risk prediction, clinical alerts, FHIR, dose and safety
  services fixed for None guards, canonical severities, dedupe and units.

### Performance
- Clinical Inbox N+1 removed (82 → 46 statements) with joined eager loads and
  a single acknowledgement lookup per result type.
- All profiled pages within budget; Patient 360 is a bounded aggregate
  (88 statements for one patient, no per-row loops) and has an explicit
  budget of 110 in the profiler.

### UI wiring (dead-feature audit)
Eleven working routes had no link or form anywhere in the UI. All are now
reachable: profile link in the sidebar, cancel prescription, cancel lab
order, verify implant, record radiation dose, raise pharmacy intervention,
preventive-care sweep, FHIR entry, AI smart orders, inline allergy and
problem edit. `doctor.view_medical_document` remains as an access-checked
legacy download path.

### Operations
- System Health page (DB, migration head vs applied, storage, secrets, CSRF,
  rate limit, debug, Gemini key, image models, latest backup).
- Backup route uses `backup/backup.py` with checksum verification and never
  renders the DSN. A backup → verify → restore-to-scratch round trip was
  executed on SQLite on 2026-09-06 (92 tables, alembic head confirmed).
- `run.py` honours `PORT` and refuses to start the development server with
  the production profile.
- CI now also checks the migration chain, seeds a scratch database, runs the
  role-by-route smoke and the query profile.

## Evidence

| Check | Result |
|-------|--------|
| Test suite | 258 tests pass (`python -m pytest tests -q`) |
| Route smoke | 2,587 role/route combinations, 0 white pages, 0 500s |
| Query profile | all hot pages within budget |
| Migration chain | single head `b7c2e9d41f05`, applies to an empty DB |
| Backup/restore | SQLite round trip verified locally |
| Browser | Command Center, Demo, Health, Patient 360, all role workspaces, RTL, dark, mobile |
| Live deployment | **not performed** (no target environment in this session) |
| PostgreSQL | **not exercised live**; schema proven via migration chain on SQLite |

## Closed in the final pass (2026-09-06)

- Reconciliation: interaction lookup is one query for the whole medication
  list (was one per pair), and discrepancy severity is normalised to
  LOW/MODERATE/HIGH/CRITICAL.
- FHIR patient index eager-loads users; the preventive sweep uses the
  follow-up relationship instead of a query per row.
- One clock: nursing, physiotherapy, dentistry, patient booking, dose and
  safety services now stamp and compare with `utcnow()` like the models and
  reception, so MAR due windows and session timestamps no longer drift by the
  server's UTC offset.

## Known remaining items (non-blocking)

- AI tools persist their recommendation audit row when generated, including
  on GET, and the radiology safety profile is created on first view. Both
  are intentional idempotent writes.
- Baseline migration has unnamed unique constraints (harmless on SQLite,
  awkward to drop on PostgreSQL; leave until a PostgreSQL target exists).
- Password complexity policy and a formal WCAG audit remain policy decisions.

## Physician experience + Patient experience + AI Clinical Copilot (2026-09-06)

Delivered on top of the audited baseline (see `docs/AI_CLINICAL_COPILOT.md`):

- AI platform: configurable budget, patient-scoped content-hashed cache,
  status pill, usage audit (no prompts stored), prompt hygiene, 429 cool-down;
  every legacy Gemini feature now goes through the same hooks. Default model
  moved to the provider alias `gemini-flash-latest` after the pinned
  `gemini-2.5-flash` was retired for new keys (404 verified live).
- Physician: minimal home (TODAY / PATIENTS / WORK / RESULTS / SAFETY / AI,
  priority tiles, collapsed sections, no provider call on load), one ✦ AI
  Copilot entry point with 32 explicit actions across seven groups, Smart
  Inbox tabs with deterministic prioritisation, local-first autocomplete
  (Tab/Esc/arrows/Enter), smart diagnosis entry, rule-based medication
  safety at prescribing (incl. allergy class cross-reactivity), explicit
  result review, pre-visit / encounter / post-visit / discharge / inpatient
  daily assistants.
- Radiology critical-finding engine: rules (authoritative, negation-aware)
  + local classifier (dependencies added) → AI-assisted alert with
  confidence and rationale → urgent task → physician + care-team
  notification → red banner → acknowledge → in progress → resolve with
  documented action / dismiss with reason → audit → configurable escalation.
- Predictive AI: Dermatology (image quality, labels, differentials, ABCDE,
  physician review), Radiology, Dentistry (chart/finding/plan/education/
  abnormality) with honest AVAILABLE / COMING SOON status.
- Patient: portal redesigned around MY HEALTH … MY NOTIFICATIONS, My Health
  Summary, safe patient AI with cross-patient isolation tests.
- SuperAdmin: AI Control Center, demo scenario, capabilities, role preview.
- Display terminology Doctor → Physician (internal ids, DB values, endpoints
  and API contracts unchanged).
- Evidence: 326 tests pass (Gemini mocked; two live probe calls only), role
  smoke 2,743 combinations clean, browser validation of the Copilot panel,
  inbox tabs, patient portal and RTL/mobile.
