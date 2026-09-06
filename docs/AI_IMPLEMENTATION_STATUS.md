# iHIS Clinical Engine — Implementation Status

Status as of 2026-09-01. This file tracks the end-to-end work on the
integrated hospital information system: unified timeline, clinical alerts,
medication reconciliation, pharmacist interventions, structured clinical
models, admin AI workbench, tests, and the Alembic migration.

## How the database is built
- The source of truth for the schema is Alembic (`flask db upgrade`), run
  under the **production** config so `db.create_all()` is NOT called first
  (development config pre-creates tables via `create_all()`, which would make
  Alembic's `CREATE TABLE` statements fail).
- Both the prior baseline chain and the new revision were validated end-to-end
  against a scratch DB and then applied to `database/ihis.db`.
- Dev DB was recreated from scratch via the migration chain (not `create_all`),
  then reseeded. Old DB retained at `database/ihis.db.backup-premigration`.

## New Alembic migration
- Revision `a0b299aeba56` ("timeline alerts reconciliation pharmacy interventions"),
  `down_revision = d95375ed9481`.
- Adds the 10 new clinical tables explicitly (`CREATE TABLE` — required because
  in dev they appeared via `create_all()` and autogenerate only emitted ALTERs):
  `timeline_events`, `clinical_alerts`, `allergies`, `problems`,
  `immunization_records`, `follow_ups`, `nursing_risk_assessments`,
  `medication_reconciliations`, `reconciliation_discrepancies`,
  `pharmacy_interventions`.
- ALTERs on existing tables (Referral urgency/notes/response/created_by etc.,
  PatientDocument uploaded_by/category/notes, DrugInteraction management/mechanism,
  Patient plus the drug interaction unique constraint).
- Batch-mode SQLite needs **named** foreign-key/unique constraints; all
  `create_foreign_key(None, ...)` were replaced with explicit names.
- The unrelated `medical_records.status` / `signed_by` model drift (pre-existing,
  not from this session) stays OUT of this migration.

## New models (`app/models.py`)
TimelineEvent, ClinicalAlert, Allergy, Problem, ImmunizationRecord, FollowUp,
NursingRiskAssessment, MedicationReconciliation (+ `home_med_list` /
`active_med_list` JSON properties), ReconciliationDiscrepancy, PharmacyIntervention.
`Allergy.patient` backref is `allergy_list` (avoids clash with the legacy
`Patient.allergies` free-text column). `AuditLog.created_at` (not `timestamp`).

## Services
- `app/services/timeline.py` — `record_event(...)`, `fetch_timeline(...)`.
- `app/services/alerts.py` — `ensure_open_alert`, `create_alert`, guarded
  ack/resolve/dismiss lifecycle. **Fixed:** `create_alert` no longer passes
  `created_by` (the model has no such column) — previously every new alert would
  crash at runtime; this also removes the unused `_uid()`/`current_user`.
- `app/services/reconciliation.py` — `run_reconciliation`, `normalize_home_medications`,
  `complete_reconciliation`.
- `app/services/search.py` — global search (HTML or JSON).

## Route wiring
- Doctor: dashboard N+1 fixed; `LabResult` import fixed (`NameError`);
  `complete_appointment` records VISIT + ensures consultation bill;
  `flag_prescription_safety` alert.
- Reception: APPOINTMENT/VISIT events. Admissions: ADMISSION/DISCHARGE events.
- Care: referral with `urgency` (Routine/Urgent/Emergency), `created_by`,
  status `SENT` default, REFERRAL event, notify receiving doctor.
- Lab / Radiology: timeline + alert (CRITICAL_LAB / CRITICAL_RADIOLOGY) wiring.
- Nursing: VITALS event + ABNORMAL_VITALS alert; vitals list N+1 fixed.
- Pharmacy: DISPENSE events; reconciliation list/create/detail/complete/resolve
  (blocks duplicate Open records; HIGH/CRITICAL findings → alerts);
  intervention create/respond (acceptance → DUPLICATE_THERAPY INFO alert;
  notify_role Doctor).
- Admin: `/admin/capacity` (rule-based capacity & no-show view, not AI). The former `/admin/ai/appointment-optimization` (one threshold) and `/admin/ai/coding-assistant` (12 keywords) were retired on 2026-09-06 and now redirect.
  (wiring AIAppointmentOptimization / AIMedicalCodingAssistant).
- SuperAdmin: real 7-day health KPIs (previously hard-coded Operational / 0 logs).
- Auth: removed unreachable doctor-registration branch.
- `base.html`: now renders flashed messages (was the root cause of earlier failures).

## Seed data (`seed.py`)
Added: `find_user` helper; referral updated to `SENT`/`Urgent`/`created_by`;
demo Problem list, verified Penicillin allergy, immunization, follow-up, an
admission reconciliation with 2 discrepancies + timeline + HIGH interaction
alert, an OPEN pharmacist intervention, and a fall-risk assessment.

## Tests
- New `tests/test_clinical_engine.py` (7 tests): reconciliation create/detail/
  list, open-record guard, discrepancy resolve + complete, intervention
  create + doctor accept (CSRF handoff via logout in `_login`; needed-to-know
  via CareTeam membership), 403 for unscoped user, admin AI pages + coding
  assistant POST.
- New `tests/test_report_access.py`: unrelated staff (Receptionist) is denied
  (403) a patient's medical-record PDF; the patient and a related Doctor get
  200. Guards the `_patient_report_access` fix.
- Full suite: **78 tests pass** (~148 s). Run scoped to the `tests/` directory
  (`python -m pytest tests -q`) — a nested project's `conftest.py` under
  `AI apps/AI-Clinical-Pharmacist/tests/` otherwise shadows `app.models` when
  the whole repo is collected.

## Access-control hardening (Phase 27 / need-to-know)
- `app/routes/reports.py` `_patient_report_access`: previously let ANY staff
  role download ANY patient's report PDF; now delegates to
  `app.access.has_need_to_know` (patient-self / Admin-SuperAdmin / documented
  relationship only).
- `app/routes/doctor.py` `patients`: removed the flawed `user_type == 'admin'`
  raw bypass; all roles now uniformly go through `accessible_patient_ids`,
  which already returns the full set for Admin/SuperAdmin.
- `app/routes/lab.py` `new_order` + `app/routes/radiology.py` `new_order`:
  POST now calls `require_patient_access` on the selected patient, and the GET
  patient dropdown is filtered to `accessible_patient_ids` (+ `-1` sentinel
  when empty). A doctor can no longer order a lab/study for an unrelated
  patient. `test_radiology_workflow.py` updated to establish the doctor-patient
  relationship (Appointment) before ordering, as realistic clinical flow does.
- `app/services/alerts.py` `created_by` crash bug (flagged earlier) remains
  fixed.

## UI data-integrity fixes
- `app/templates/clinical/patient_360.html` forms now expose every field the
  backend already saved: Allergy (onset, notes), Problem (status, notes),
  Immunization (lot_number, site, next_due, notes) — previously these could
  never be entered through the UI.
- `app/templates/care/referrals.html`: new-referral form has an `urgency`
  select (Routine/Urgent/Emergency) matching `care.new_referral`; list table
  gained a Urgency column (empty-row colspan bumped 7→8).

## Notes / decisions
- CSRF in tests: token sourced from a page that always renders a form
  (reconcile page), and re-login forces `/auth/logout` first because a logged-in
  session's `/auth/login` does not hand out a fresh token.
- Reconcile page with an existing Open record shows an in-page warning
  ("open reconciliation for this patient") rather than only a flash.
- Intervention respond form renders only for OPEN + `INTERVENTION_RESPOND`
  permission (this is correct; a no-OBJECT index page therefore has no token).
- Verification order: scratch-DB full migration → apply to dev DB → seed →
  boot smoke (226 routes) → unit/integration suite.

## Remaining / not done
- No changes to `app/services/ai/ai_interfaces.py` recommendations beyond the
  admin wiring (the optimization/ICD engines are the existing stubs).
- No new columns beyond the ones listed; `ClinicalAlert.created_by` intentionally
  not added (see alerts service fix).
- Further access-control hardening deferred (billing/lab/radiology worklist
  detail+action routes are role-scoped department functions with their own
  membership model; not covered by the need-to-know patient relationships).
  The `new_order` creation path (the sensitive cross-patient write) is hardened.

## Session 2026-09-02 — task coverage, E2E simulation, Cashier permissions

Status as of 2026-09-02. Continuing on from the file header, this session closed
out the remaining roadmap items:

### Referral task regression fixed (`tests/test_task_generation.py`)
- `test_referral_to_doctor_creates_referral_task` was flaky: the receiving
  doctor's `/tasks/my-tasks` showed "No open tasks" because the tri-actor
  assertion re-logged into a *different* user without clearing the earlier
  session. `_login` now performs `client.get('/auth/logout')` first (the same
  handoff pattern already used by the clinical-engine / pharmacy workflow
  tests). Both task-generation tests pass.

### End-to-end hospital simulation (`tests/test_hospital_simulation.py`)
- New `HospitalSimulationTestCase` drives one patient through the entire real
  business lifecycle across every major actor in a single coherent run:
  1. patient books an appointment via the portal;
  2. doctor completes the visit → consultation bill auto-generated
     (asserts a `family consultation_fee` bypasses the 0-fee / empty
     ServiceCatalog shortcut and a bill is really created);
  3. doctor orders a lab test → `LAB` task routed to the lab queue;
  4. lab technician enters + verifies the result → `Lab` bill auto-generated;
  5. doctor writes a prescription → `PHARMACY` task routed;
  6. pharmacist dispenses against stock (ledger decremented to 90);
  7. cashier settles the consultation + lab (+ any dispensing) bills;
  8. patient portal reflects the journey (prescriptions + lab results render);
  plus a second test asserting the receptionist-scoped booking path and the
  resulting patient in-app notification.
- Confirms the cross-cutting plumbing (timeline, routed tasks, auto-billing,
  need-to-know) hangs together for a realistic multi-actor journey — not just
  per blueprint in isolation.

### Cashier role permission gap fixed (`app/permissions.py`)
- The `Billing` STAFF constant already included `Cashier`, and
  `record_payment` requires `PAYMENT_RECORD`, but `Cashier` had **no** entry in
  `ROLE_PERMISSIONS` — a logged-in cashier effectively had zero permissions and
  403'd on every billing action. Added a focused `Cashier` `ROLE_PERMISSIONS`
  entry (`PATIENT_VIEW, BILL_VIEW, BILL_EDIT, PAYMENT_RECORD, TIMELINE_VIEW,
  SEARCH_GLOBAL`) so the money-handling front-desk role actually works.
  `seed_permissions` is idempotent, so this applies harmlessly on an existing DB.

### UI / template audit
- Scanned all 133 templates plus `app/` Python/static/JS for
  TODO/FIXME/XXX/coming-soon/under-construction/placeholder-stub content.
  **Clean** — all `placeholder=` matches are legitimate bilingual form hints or
  real "expiring soon" medication expiry UI; no unfinished stubs remain.

### Full suite
- Now **92 tests pass** (~166 s). Run scoped to `tests/`
  (`python -m pytest tests -q`) — the nested `AI apps/AI-Clinical-Pharmacist`
  `conftest.py` still shadows `app.models` when the whole repo is collected.
- App boots cleanly with `FLASK_CONFIG=development` after the permission change.

## Session 2026-09-02 (cont.) — production-readiness security fixes

### Open-redirect fix (RED)
- `app/routes/auth.py`: Added `_safe_next()` helper that rejects protocol-relative
  URLs (`//evil.example`) and absolute URIs (`https://evil.example`). The login
  `?next=` parameter now calls `_safe_next()` instead of bare `startswith('/')`.
- 3 regression tests: protocol-relative blocked, absolute blocked, local path allowed.

### Client-injected status fix (YELLOW)
- `app/routes/api.py` `create_prescription` (line 439): changed from
  `status=data.get('status', 'Active')` to `status='Active'` (server-controlled).
- `app/routes/api.py` `create_referral` (line 536): changed from
  `status=data.get('status', 'Pending')` to `status='Pending'` (server-controlled).
- 2 regression tests verify that sending `Cancelled`/`COMPLETED` is ignored.

### AI data minimization (YELLOW)
- `app/services/ai/clinical_pharmacist.py` `_build_prompt`: removed patient
  `full_name` and `MRN` from the Gemini medication-review prompt. Retains only
  clinically necessary data (age, gender, meds, allergies, diagnoses, labs).

### Production readiness audit doc created
- `docs/PRODUCTION_READINESS_AUDIT.md`: severity-keyed log covering baseline,
  findings, fixes, and remaining items. Updated to 92 tests / 4 fixed items.

## Session 2026-09-02 (cont.) — deep production-readiness hardening (phases 2–48)

### Schema / database integrity
- **PostgreSQL compatibility (Phase 5):** Added `nullable=False` to 13 Boolean
  columns that previously relied on Python-side defaults only; added `index=True`
  to 20+ high-volume `patient_id` foreign keys across the clinical tables.
- **Migration (Phase 6):** Alembic chain now ends at head `59f96da6bbf3`,
  built on `12a33945697d` (adds payment idempotency unique constraint on
  `payments(bill_id, reference)`) and `59f96da6bbf3` (indexes clinical
  hot-path FK columns: `medical_records.doctor_id`, `appointments.doctor_id`,
  `prescriptions.doctor_id`, `lab_orders.doctor_id`/`test_id`,
  `radiology_orders.doctor_id`, `diagnoses.doctor_id`, `prescription_items.*`,
  `dispensing_records.*`, `payments.bill_id`, `bill_items.bill_id`,
  `medication_administrations.*`, dental/therapy/rehab patient+provider FK
  columns, `referrals.from/to_doctor_id`, `tasks.assigned_to/created_by`),
  which supersede the earlier `6e4b0dc64295` "index patient FKs
  and enforce boolean not-null". Verified `flask db
  upgrade` succeeds and head is current.

### Production config hardening
- `create_app('production')` now **raises** if `SECRET_KEY` is weak/absent OR if
  `DATABASE_URL` is absent/points to SQLite — running production on SQLite or a
  dev secret is no longer possible (previously only a warning).
- `PASSWORD_MIN_LENGTH` already 12 in production (from prior session).

### Rate limiting (Phase 15)
- Added Flask-Limiter 4.1.1 to `requirements.txt` and wired into `create_app`.
- Login: `10 per minute`; register: `5 per hour`.
- `RATELIMIT_ENABLED` config flag (disabled in the `testing` config); default
  in-memory storage, configurable via `RATELIMIT_STORAGE_URI` (e.g. Redis for
  multi-worker production).
- Reference nginx rate-limit zones provided in `deployment/nginx.conf`.

### Structured logging + request correlation (Phases 20–22)
- New `app/services/logging.py`: adds per-request access logging (method, path,
  status, latency in ms) with a `request_id`; honors inbound `X-Request-ID`;
  `get_request_id()` exposes the correlation id on `g`. Wired into `create_app`.

### Production-safe error handling (Phase 18)
- Error handlers for 400/403/404/409/429/500/503. Render HTML error templates
  (both logged-in and logged-out base blocks) or JSON for `/api/` requests.
  500 handler rolls back the DB session and never exposes internal details.
- Templates created in `app/templates/errors/{400,403,404,500}.html`.

### Seed safety (Phase 4)
- `seed.py` refactored to support `--roles-only` (safe for production). The
  full demo seed refuses to run when `FLASK_CONFIG=production`.

### Backup / deployment / ops
- `backup/backup.py`: PostgreSQL `pg_dump` + SQLite copy, SHA-256 checksums,
  retention, `--verify`.
- `scripts/preflight_check.py`: pre-deployment gate (secret/db checks, git
  cleanliness, production boot).
- `deployment/nginx.conf`, `deployment/.env.production.example`.
- Docs: `DEPLOYMENT_GUIDE.md`, `BACKUP_AND_RECOVERY.md`, `SECURITY_OPERATIONS.md`,
  `PRODUCTION_READINESS_MATRIX.md` (updated to 48 phases / PRODUCTION-READY
  CANDIDATE).

### Tests
- New `tests/test_production_hardening.py` (19 tests): error-handler JSON/HTML
  over 400/401/403/404/409/422/429/500/503, 500 no-leak, request-id
  correlation, production config validation (weak secret / missing DB rejected
  via subprocess), login rate limiting (subprocess), security headers, static
  private-upload blocking, HSTS + cookie-security config in production, and
  backup-script import portability.
- New `tests/test_ai_media_privacy.py` (7 tests): static private paths blocked,
  role-gated `ai_media`, path-traversal blocked, tooth graceful when model
  missing, private file served to authorized role.
- New `tests/test_ai_resilience.py` (3 tests): missing `GEMINI_API_KEY` renders
  200 not 500; provider exception returns a structured error (no raise); the
  Gemini prompt contains no name/MRN/PII identifiers.
- New `tests/test_inventory_concurrency.py` (2 tests): atomic dispense cannot
  drive stock negative or lose updates on a double dispense.
- Full suite: **124 passed, 0 failures** (`python -m pytest tests -q`, ~3 min).

## Session 2026-09-04 — Premium frontend redesign (Phases 1–5)

Reframed the product as "one connected hospital operating system" with an
executive SuperAdmin Command Center, redesigned Patient 360, a Doctor
workspace, and a professional visual identity. All dashboard data comes from
real DB queries — nothing is hardcoded.

### Phase 1 — Design system CSS (`app/static/css/style.css` v3.0)
- Enhanced design tokens (spacing, radii, elevation, semantic color vars with
  `-light`/`-dark`/`-bg` variants), premium typography scale (Inter + Cairo for
  AR), dark-mode token support.
- Component styles: care strip, activity panel, command-center grid (`cc-grid`,
  `cc-panel`, `metric-row`), operational lists, admin/role-preview banners,
  capability cards, command palette, toast system, stat rows, print + reduced
  motion + focus-visible + skip-link accessibility.

### Phase 1 — SuperAdmin backend (`app/routes/super_admin.py`)
- Executive `dashboard` pulls ~40 real DB metrics (health, users by role,
  today's operations, lab/radiology/pharmacy volumes, revenue, tasks, alerts,
  admissions, referrals, dental, physio, security, audit feed).
- New `/capabilities` (Platform Capabilities Discovery Center): 10 groups × ~60
  features, all real.
- New `/preview/<role>` + `/exit-preview` for role-preview mode.
- Fixed `Bill.total()` — computed Python method, not a SQL column; outstanding
  revenue now summed in Python.

### Phase 2 — Base template + context processor
- `app/templates/base.html`: premium grouped sidebar navigation with section
  labels (Command Center / Clinical / Operations / Diagnostics / Medication /
  Specialties / Finance / Intelligence / Administration), role-aware nav,
  Ctrl+K command palette markup, Global Admin View banner with a live
  "Preview as role..." dropdown, Role Preview banner, sidebar user footer with
  theme + logout, topbar search/task/notification/language.
- `app/__init__.py` `register_context_processors`: `_get_effective_roles()`
  (uses the preview role when a SuperAdmin is previewing), grouped nav builder,
  `is_superadmin_real()`, `is_previewing()`, `effective_role_name()`.
- Fix: `group.items` dict-key collision with the Jinja2 dict method → use
  `group['items']`; `current_user` scoping in the context-processor lambdas.

### Phase 3 — SuperAdmin dashboard + capabilities templates
- `super_admin/dashboard.html`: Executive Command Center — critical KPI row,
  six department operation panels, role-distribution + security panel, recent
  activity (audit + tasks), specialty module totals.
- `super_admin/capabilities.html`: capability groups with feature cards,
  category + role chips, live stats.

### Phase 4 — Patient 360 (`clinical/patient_360.html` + route)
- Premium header with richer meta (MRN/gender/DOB/blood/phone/admitted),
  active-admission link, severity-coded alert chips.
- New **care strip** of latest vitals (temp/BP/HR/RR/SpO2/pain) from real
  `VitalSign` data (`latest_vitals` in the route).
- Count badges on each panel header; enriched structured records with chips and
  icons.

### Phase 5 — Doctor workspace (`doctor/dashboard.html` + route)
- Six-KPI stat row (appointments, pending labs, pending radiology, at-risk
  patients, my tasks, clinical alerts).
- "Recent Patients" panel built from the doctor's own appointment history.
- "My Tasks" and "Clinical Alerts" side panels from real Task/ClinicalAlert data.
- Fixed Task column (`assigned_to` not `assignee_id`) and endpoint
  (`clinical.alerts_all` not `clinical.alerts`).

### Verification
- All redesigned routes render 200 as the correct role/scope.
- **124 tests pass, 0 failures** (no regressions).
- Role preview → exit flow verified end-to-end.

---

## Session 2026-09-04 — Complete AI Feature Implementation

### Overview
Added 8 new AI service modules, upgraded 4 existing heuristic stubs to
Gemini LLM, created 6 new templates, and added 8 new routes. Every new
feature gracefully falls back to heuristic rules when `GEMINI_API_KEY` is
not set. All AI outputs are logged to the `AIRecommendation` audit trail
model.

### New Service Files (`app/services/ai/`)

| File | Class | Description |
|------|-------|-------------|
| `gemini_base.py` | `GeminiBase` | Shared base class — API call, JSON parsing, `_collect_patient_context()`, audit logging |
| `ai_diagnosis.py` | `AIDiagnosisSupport` | **Gemini** differential diagnoses — ranked differentials with ICD-10, confidence, reasoning, red flags, next steps |
| `ai_lab_interpretation.py` | `AILaboratoryInterpretation` | **Gemini** lab interpretation — clinical significance, severity, possible conditions, follow-up tests |
| `ai_risk_prediction.py` | `AIPatientRiskPrediction` | **Gemini** risk narrative — overall risk (Critical/High/Moderate/Low), individual factors, mitigation strategies, trajectory |
| `ai_medical_coding.py` | `AIMedicalCodingAssistant` | **Gemini** ICD-10 coding — codes from free text or from documented diagnoses, specificity notes |
| `ai_clinical_notes.py` | `AIClinicalNotes` | **Gemini** SOAP note generator — Subjective/Objective/Assessment/Plan from clinical data |
| `ai_smart_orders.py` | `AISmartOrders` | **Gemini** smart order sets — lab, imaging, prescriptions, non-pharmacologic, follow-up |
| `ai_patient_communication.py` | `AIPatientCommunication` | **Gemini** patient communication — condition summaries, medication guides, self-care instructions (EN/AR) |
| `ai_clinical_alerts.py` | `AIClinicalAlertEngine` | Rule-based + LLM alert engine — critical labs, vital deterioration, drug interactions, readmission risk, sepsis screening |

### Upgraded Existing Services
- `AIDiagnosisSupport` — from 7-keyword dictionary → full Gemini differential diagnosis with patient context
- `AILaboratoryInterpretation` — from threshold comparison → Gemini clinical interpretation with severity and follow-up
- `AIPatientRiskPrediction` — from additive score → Gemini narrative risk with individualized factors
- `AIMedicalCodingAssistant` — from 12-keyword ICD map → Gemini free-text ICD-10 coding

### New Routes (`app/routes/ai.py`)
| Route | Method | Description |
|-------|--------|-------------|
| `/ai/soap-notes/<patient_id>` | GET | Retired → redirects to Patient 360 with `?copilot=doc.structure` |
| `/ai/smart-orders/<patient_id>` | GET | Retired → `?copilot=reasoning.investigations` |
| `/ai/patient-communication/<patient_id>` | GET | Retired → `?copilot=comm.summary` |
| `/ai/medical-coding/<patient_id>` | GET/POST | ICD-10 coding from text or diagnoses |
| `/ai/clinical-alerts` | GET | View active clinical alerts |
| `/ai/clinical-alerts/scan` | POST | Full alert scan on all active patients |
| `/ai/clinical-alerts/<id>/read` | POST | Mark alert as read |
| `/ai/ai-dashboard` | GET | Legacy URL, renders the AI Hub (role-filtered) |
| `/ai/hub` | GET | AI Hub: every AI capability the signed-in user can reach, with an honest AVAILABLE / LIMITED / COMING SOON status |

### New Templates (`app/templates/ai/`)
- (`soap_notes.html`, `smart_orders.html`, `patient_communication.html`, `summary.html`, `diagnosis_support.html`, `radiology.html`, `prescription.html`, `analytics.html` were removed on 2026-09-06; the Copilot panel covers them)
- `patient_communication.html` — Patient communication with language/topic selection
- `medical_coding.html` — ICD-10 coding with text input and diagnosis mode
- `clinical_alerts.html` — Clinical alert feed with severity badges
- `ai/hub.html` — AI Hub (replaces the old AI Command Center): role-filtered catalogue built by `app/services/ai/hub.py`

### Audit Trail
Every AI recommendation (diagnosis, lab interpretation, risk, coding, SOAP,
order set, patient communication) is logged to `AIRecommendation` with:
- `patient_id`, `recommendation_type`, `content`, `confidence_score`
- The AI Dashboard displays total/applied recommendations and breakdown by type

### Clinical Alert Engine
Scans all active patients for:
- **Critical lab values** (glucose, potassium, sodium, hemoglobin, etc.)
- **Vital sign deterioration** (tachycardia, hypertensive emergency, hypoxia, high fever)
- **Drug interactions** (major/severe/contraindicated from DrugInteraction table)
- **30-day readmission risk** (recently discharged patients)

### Test Results
- **124 tests pass, 0 failures** (no regressions)

---

## Session 2026-09-04 — Sandbox-Ready seed data (5 reference patients)

Added `seed_patients.py`: a self-contained, idempotent seed that creates the
5 named reference patients from the project spec so that every screen and
button is immediately testable against real, related records (no empty pages,
no fabricated dashboard numbers). Development-only — refuses to run against a
server (non-SQLite) database.

### The five scenarios
- **P1 Ahmed (P10001)** — emergency cardiology: HTN, unstable angina I20.9,
  critical troponin 5.2 ng/mL, creatinine 1.6, CXR, chest/ECG lab + radiology +
  consultation bills, `CRITICAL_LAB` alert, imaging safety profile.
- **P2 Fatima (P10002)** — T2DM E11.9 + hypothyroidism E03.9, severe penicillin
  allergy, medication reconciliation + discrepancy, pharmacy intervention
  (amoxicillin → azithromycin), `ALLERGY` alert, HbA1c 8.9%.
- **P3 Khaled (P10003)** — femur fracture S72.3 + ORIF, admission (bed 12),
  femur X-ray **and** post-op CT (both `PERFORMED`), full radiation dose
  records (X-ray Air Kerma 1.32 mGy; CT DLP 612 mGy·cm / CTDIvol 15.2),
  imaging safety profile, physiotherapy assessment/plan/session + FIM, radiology
  + physio bills, care team.
- **P4 Noora (P10004)** — dental: chart (26 Caries, 11 Crown), treatment plan +
  2 procedures, OPG image, tetanus overdue → `OVERDUE_FOLLOWUP` preventive
  alert, dental follow-ups (+7d, +90d).
- **P5 Sara (P10005)** — pediatric asthma J45.9, ventolin prescription, normal
  CXR, asthma MDT care team + case, portal messages, follow-up.

### Key implementation details
- Every patient has a matching `patient_<name>` portal user; clinicians
  (`dr_*`) + dentist `dr_youssef` (dual Doctor+Dentist profiles). All accounts
  use `123456`.
- Relationships are wired so the need-to-know access checks
  (`app/access.py`) pass — each clinician is linked via appointment / order /
  prescription / care-team / order-owner records.
- Missing catalog items (Levothyroxine, Insulin Glargine, Nitroglycerin,
  Azithromycin) are created on demand by `_ensure_medications()`, which also
  guarantees inventory rows (fixes the earlier `prescription_items.medication_id`
  NOT-NULL failure).
- `--rebuild` flag: `python seed_patients.py --rebuild` wipes just the 5
  sandbox patients (leaf-first, FK-safe) and recreates them — keeps the script
  maintainable as scenarios evolve.
- Idempotency verified: a second run skips all five ("already seeded").
- Full suite **148/148 passing** (no regressions).

---

## Session 2026-09-05 — Open-source benchmark, feature harvesting & native reimplementation

Applied the master prompt "open-source benchmark, feature harvesting & native
reimplementation" against OpenMRS / Bahmni / OpenEMR / fhir-ui as references.
Research is recorded in `docs/` (see `OPEN_SOURCE_BENCHMARK.md`,
`FEATURE_GAP_MATRIX.md`, `OPENEMR_GAP_ANALYSIS.md`,
`OPEN_SOURCE_ATTRIBUTIONS.md`, `OPEN_SOURCE_VALUE_SCORE.md`). **No reference
code was copied** — the license audit concluded OpenEMR is GPL-3.0 (roadmap
reference only), OpenMRS core is MPL-2.0 (ESM chart/dispensing status
NOT_VERIFIED), Bahmni frontend licence text is missing (NOT_VERIFIED), and
fhir-ui is MIT — so every harvested feature was reimplemented natively from
patterns/behaviour only.

### Delivered features (native)
1. **Order sets** (`/clinical/order-sets*`) — `OrderSet` + `OrderSetItem`
   (`app/models.py`). Reusable LAB / RADIOLOGY / MEDICATION / REFERRAL bundles.
   **Newly created sets are `is_active=False`** — they must be explicitly
   activated before they can be applied (safety default: an unreviewed set is
   never applicable to a patient). Applying materialises real `LabOrder` /
   `RadiologyOrder` / `Prescription` / `Referral` records plus `Task` rows and
   redirects to patient 360; the apply POST is guarded by
   `@patient_access_required`.
2. **Clinical templates** (`/clinical/templates*`) — `ClinicianTemplate` with
   JSON `sections` (SOAP / CONSULT / DISCHARGE / PROCEDURE / NURSING), specialty
   scoping, enabled/disabled toggle.
3. **Unified Clinical Inbox** (`/clinical/inbox`) — unreviewed lab results,
   radiology reports, open alerts, pending referrals, interventions, drafts,
   my tasks and reminders in one place, with counts. Result acknowledgement
   via `ResultAcknowledgement` (idempotent upsert; kind `CRITICAL` for critical
   labs else `REVIEW`) — `POST /clinical/inbox/ack` guarded by
   `require_patient_access`.
4. **Clinical Reminder engine** (`/clinical/reminders*`) —
   `app/services/reminders.py` (`scan_due_reminders`, idempotent dedupe per
   patient/type/source, auto-close), `ClinicalReminder` model, ack via
   `reminder_done`.
5. **Smart patient header / safety context (P0 #1/#11)** — new reusable
   `PatientSafetyContext` service (`app/services/patient_safety.py`) returning
   active allergies, open problems, open alerts and active medications (deduped,
   cancelled items excluded), plus the reusable `_patient_header.html` partial
   (name/MRN/gender/DOB/blood type, active admission, latest vitals, safety
   chips, active-meds chips). Wired into **every single-patient clinical page**:
   doctor (overview/detail/360/EMR add+edit/prescriptions/lab/radiology), AI
   (summary, diagnosis-support, rehab, medication-review, soap-notes,
   smart-orders, patient-communication, medical-coding, lab/radiology
   interpretation, prescription check, skin/fracture/tooth), pharmacy reconcile,
   nursing MAR, and dentistry (chart/record/procedures/imaging/treatment-plan).
   The `_patient_safety_strip.html` chips remain embedded in Patient 360.
6. **Verified Clinical Summary** (`/clinical/summary/<id>`) — consolidated
   allergies, problems, active prescriptions, latest vitals, lab results,
   radiology, admissions, immunizations, open alerts; need-to-know gated.

### New permissions (`app/permissions.py`)
`ORDER_SET_VIEW/CREATE/EDIT`, `TEMPLATE_VIEW/CREATE/EDIT`, `INBOX_VIEW`,
`RESULT_ACK`, `REMINDER_VIEW`, `REMINDER_ACK`, `CLINICAL_SUMMARY_VIEW` — added
to Doctor / Admin / SuperAdmin `ROLE_PERMISSIONS`. New models live in
`app/models.py`; nav menu entries added in `app/__init__.py`.

### Migration
- `migrations/versions/f1b1a6907bef_add_clinical_order_sets_templates_reminders_ack.py`
  (revision `f1b1a6907bef`, down `8a75a265845d`): creates the 5 new tables
  (`order_sets`, `order_set_items`, `clinician_templates`, `clinical_reminders`,
  `result_acknowledgements`) with named FKs; verified `upgrade()`/`downgrade()`
  on a fresh SQLite DB. Dev DB stamped at this head. (Note: the full migration
  chain cannot replay from empty on a fresh dev DB because the dev DB was
  previously built via `create_all` — pre-existing drift, not from this cycle.)
- `seed.py` now also creates a demo **Admission Workup** order set (CBC + LFT +
  Metformin; active) and a **Follow-up Consultation** template (CONSULT, 4
  sections), idempotently.

### Tests
- New `tests/test_order_sets.py` (5), `tests/test_clinical_inbox.py` (5),
  `tests/test_clinical_templates.py` (4), `tests/test_reminders.py` (6),
  `tests/test_clinical_summary.py` (4), `tests/test_patient_header_safety.py` (9).
- Three issues surfaced and fixed during verification:
  - **CSRF in ack test**: the out-of-scope ack POST failed CSRF (400) because
    the inbox page lists only in-scope results, so no ack form/`csrf_token` was
    rendered; the test now seeds an in-scope result first so the page carries a
    valid token while the out-of-scope post is still correctly rejected (403).
  - **Order sets now default to inactive** (`is_active=False` on create) so a
    freshly created set is not applicable until reviewed + toggled on — this
    also made the picker route's inactive-guard testable end-to-end.
  - **Summary lab label**: `LabResult` renders via `res.order.test.test_name`
    (the relationship chain), not a non-existent `res.test`.
- Full suite: **200 passed, 0 failures** (`python -m pytest tests -q`,
  ~5:12). The 9 header tests assert the safety header/strip and allergy/problem
  chips render across doctor, AI, pharmacy, nursing and dentistry pages, that
  the header precedes the EMR form, and that out-of-scope patients never leak
  it.

### Docs
- `docs/ARCHITECTURE.md`: added "Clinical Workbench" section; updated
  `docs/AI_IMPLEMENTATION_STATUS.md`, `docs/RELEASE_READINESS.md`,
  `docs/RELEASE_CANDIDATE.md`.

## Session 2026-09-05 (continuation) — Safety-gap closure: critical labs + clinical list badging

Continued the same master prompt, targeting the remaining P0/P1/P2 gaps.

### Delivered features (native)
1. **P0 #2 — Lab critical-value auto-flagging & acknowledgement** —
   `LabTestCatalog` gained `critical_low` / `critical_high` (Float) and
   `critical_notes` (String(250)). `app/utils.py`: `evaluate_lab_criticality`
   (tri-state True/False/None), `apply_lab_criticality`, and extended
   `apply_lab_abnormality(..., manual_abnormal=False)`. On `enter_result` every
   branch now derives flags and, when critical, persists
   `is_critical`, notifies Doctors (`notification_type='critical'`) and upserts
   an idempotent open `CRITICAL_LAB` alert (severity `CRITICAL`).
   `verify_result` re-derives flags at verification — thresholds added *after* a
   result was entered still flag/escalate — before the abnormal branch.
   `ResultAcknowledgement` (from the prior clinical-inbox cycle) provides the
   acknowledgement log (kind `CRITICAL`).
2. **P1 #13 — Lab worklist** — `/lab/orders` rewritten: status / priority /
   critical-only (`is_critical` join) filters, accession/order search (`q`),
   and the orders table now shows specimen type + collection time with a
   specimen-status badge.
3. **P1 #14 — Pharmacy expiry/low-stock badging** — inventory route classifies
   each item (`expired` > `low` > `expiring` ≤90d > `ok`) with `?f=` tabs
   (All / Low Stock / Expiring Soon / Expired); rows highlight when expired and
   badges keep correct precedence (an expired *and* low item shows both, never
   masked).
4. **P1 #15 — Partial-dispensing visibility** — prescriptions queue excludes
   `Dispensed`/`Cancelled` and floats partially dispensed rx to the top; queue
   and detail pages show a per-rx progress block ("still owed N units") and
   per-item remaining markers.

### Migration
- `migrations/versions/8f3c0d1a2e9b_add_critical_thresholds_to_lab_catalog.py`
  (revision `8f3c0d1a2e9b`, down `f1b1a6907bef`): adds the three
  `lab_test_catalog` columns; applies cleanly to the dev DB (new head).
- `seed.py`: `LAB_TESTS` now 6-tuples incl. critical bounds for CBC `(0.5, 50)` /
  HbA1c / Thyroid; thresholds backfilled on existing rows only when unset.

### Tests
- New `tests/test_lab_critical.py` (6) and `tests/test_pharmacy_badging.py` (5,
  incl. one fix: an expiring-but-fully-stocked item now shows **In Stock**
  alongside its expiry chip).
- Full suite: **211 passed, 0 failures** (`python -m pytest tests -q`).
- `docs/FEATURE_GAP_MATRIX.md` roll-up: **done 14, partial 1, gap 5
  (rejected 1)**; #2, #13, #14, #15 flipped to done.

## Session 2026-09-05 (continuation 2) — Clinical summary completeness + observation density

1. **P1 #12 completion — Clinical summary (verified)** — added a **Care Team**
   card (dormant `CareTeam`/`CareTeamMember` model now surfaced; route joins
   members for the patient) and a **print action** (`window.print()`) with
   `@media print` rules that hide the sidebar/topbar and keep cards intact.
2. **P3 #20 — Reference ranges inline (observation density)** — the normal
   range is now shown next to every lab value: doctor lab-results table,
   patient portal, lab worklist, clinical summary, patient overview and patient
   360 (values now also carry their unit). This surfaced a real defect:
   `doctor/lab_results.html` referenced non-existent fields (`order.test.name`,
   `order.result_value`, `order.created_at`) and always broke kind of templates —
   rewritten against real `LabOrder`/`LabResult` fields incl. status mapping,
   critical/abnormal badges and an AI action when a verified result exists.

### Tests
- `tests/test_clinical_summary.py` +1 (care team + print), new
  `tests/test_lab_ref_range.py` (4: doctor/patient/worklist/summary render the
  range and value).
- Full suite: **216 passed, 0 failures**.
- `docs/FEATURE_GAP_MATRIX.md` now **done 16, partial 0, gap 4 (rejected 1)**;
  #12 and #20 flipped to done. All P0 and P1 items closed; remaining gaps are
  P2 #16/#17 (deferred per matrix) and P3 #18/#19 (out of cycle scope).

## Session 2026-09-05 (continuation 3) — Recall board (P2 #17)

1. **P2 #17 — Scheduling recall board** — new `clinical.recall_board` route
   (`/clinical/recall-board`, `REMINDER_VIEW`) presents scheduled follow-ups and
   open clinical reminders in one board, bucketed **Overdue / Due Today /
   Upcoming** with live count chips and an overdue-review alert. Follow-ups come
   from `FollowUp` (status `Scheduled`), reminders are auto-materialised by the
   recall engine then split by due date; every row links to the patient 360 and
   the reminder rows carry a one-click **Done** form posting to the existing
   `clinical.reminder_done` ack action. New nav entries on the clinical workload
   menus (`app/__init__.py`).

2. **Recall-engine hardening** — `scan_due_reminders` was auto-closing *manual*
   reminders: its source-gone `FOLLOWUP`/`IMMUNIZATION` close pass treated
   reminders without a linked source (`source_type`/`source_id` `None`) as
   orphaned and marked them `DONE`, so a clinician-created reminder could vanish
   on any scan. The close pass now skips source-less reminders — engine manages
   only what the engine creates.

### Tests
- New `tests/test_recall_board.py` (2: board buckets overdue/due-today/upcoming
  across follow-ups + reminders; one-click Done persists and hides the item).
- Full suite: **218 passed, 0 failures**.
- `docs/FEATURE_GAP_MATRIX.md` now **done 17, partial 0, gap 3 (rejected 1)**;
  remaining gaps are P2 #16 (deferred) and P3 #18/#19 (out of cycle scope).

## Session 2026-09-05 (continuation 4) — In-patient dashboard widget (P2 #16)

1. **P2 #16 — In-patient dashboard widget** — the ward module already carried
   full Ward/Bed/Admission data (incl. `days_stayed()`, `expected_discharge`),
   so the previously "deferred" gap is now justified and closed. `admissions.dashboard`
   gained:
   - **Ward census**: per-ward occupancy board (occupied/total + occupancy bar,
     free beds, houseful/ICU highlight, fully-populated wards edged in danger colour);
   - **Needs Attention**: admitted patients who have *overstayed* their
     `expected_discharge` (with `+N` days) or carry an open HIGH/CRITICAL
     `ClinicalAlert` (e.g. critical labs), each linking to the patient 360;
   - discharged patients drop out of both widgets and their bed frees, verified
     end-to-end.

### Tests
- New `tests/test_inpatient_board.py` (3: census figures per ward incl. full ward,
  overstay + critical-alert flagging with discharged exclusion, post-discharge
  bed frees and count drops, nurse access).
- Full suite: **221 passed, 0 failures**.
- `docs/FEATURE_GAP_MATRIX.md` now **done 18, partial 0, gap 2 (rejected 1)**;
  remaining gaps are P3 #18/#19 (out of cycle scope).

## Session 2026-09-05 (continuation 5) — FHIR resource view (P3 #18)

1. **P3 #18 — FHIR resource view** — a read-only FHIR R4 feed, surfacing the
   structured clinical record to FHIR-aware consumers with zero writes:
   - `app/services/fhir.py` serializers map native models to standard
     resources — `Patient`, `Observation` + `DiagnosticReport` (per lab order,
     with critical/abnormal `interpretation` and `referenceRange`), `Condition`
     (ICD-10 coded diagnoses + problem list), `AllergyIntolerance`,
     `Immunization` (incl. next-due extension), `MedicationRequest` (with
     dosage instructions per item), `Encounter` (from admissions and
     appointments).
   - `app/routes/fhir.py` (registered at `/fhir`): `Patient/{id}` resource,
     `Patient/{id}/$everything` searchset Bundle, a scoped patient index, and a
     browser view (`/view`) listing resources grouped by type, all served as
     `application/fhir+json`. Every endpoint requires `TIMELINE_VIEW` plus the
     standard patient need-to-know gate (403 otherwise).
   - Patient 360 header gains a **FHIR** action linking to the browser view.

### Tests
- New `tests/test_fhir_view.py` (5: Patient resource shape/identifier,
  `$everything` bundle total + full resource-type coverage + observation value,
  browser view, need-to-know 403 across all three surfaces, scoped index).
- Full suite: **226 passed, 0 failures**.
- `docs/FEATURE_GAP_MATRIX.md` now **done 19, partial 0, gap 1 (rejected 1)**;
  the only remaining gap is #19 (dynamic form-designer DSL), deliberately
  out of scope — iHIS prefers native forms.



---

## Session 2026-09-05/06 — Full-system takeover audit

Scope and evidence live in `docs/SYSTEM_AUDIT_REPORT.md`. AI-specific outcomes:

- `gemini_base.py`: API key travels in the `x-goog-api-key` header (never the
  URL), provider errors are sanitised (`AIServiceError`), connect/read
  timeouts `(10, 90)`; regression tests assert no key leakage.
- `clinical_pharmacist.py`, `ai_patient_communication.py`,
  `ai_clinical_notes.py`, `ai_diagnosis.py`, `ai_lab_interpretation.py`,
  `ai_risk_prediction.py`, `ai_smart_orders.py`, `ai_medical_coding.py`,
  `ai_clinical_alerts.py`: None guards, sanitised errors, canonical
  severities/types, deduped alerts, no patient names in prompts.
- Image tools (`fracture_detection.py`, `tooth_segmentation.py`,
  `skin_lesion_classification.py`) fail soft when their runtime is missing.
  The skin-lesion page reported a missing model only when the server ran on a
  system interpreter without `timm`; running from the project venv restores
  the result card (verified in the browser on port 5000).
- AI smart-order suggestions are now linked from the doctor's patient page.
- RadiologyTechnician role gets `AI_IMAGE_ANALYSIS`; Radiologist gets inbox,
  alerts and timeline.
- Full suite: **257 tests pass**.
