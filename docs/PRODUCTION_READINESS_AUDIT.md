> Note: this is a point-in-time audit log. The authoritative current status is
> `docs/GO_LIVE_MATRIX.md` and `docs/RELEASE_READINESS.md` (**124 tests green**,
> single migration head `59f96da6bbf3`).

# iHIS Production Readiness Audit

Status as of 2026-09-02. This is the live, continuously-updated log for the
production-readiness gate. Each phase is audited, findings are classified by
severity, and RED/ORANGE items are fixed with regression tests before the next
phase.

## Severity key
- RED    = production blocker (must fix before release)
- ORANGE = high-risk (fix at release gate unless mitigated)
- YELLOW = medium-risk (should fix)
- BLUE   = improvement
- GREEN  = acceptable

## Baseline (Phase 1)
- Git branch: `main`; many uncommitted working-tree changes (feature work in
  progress from prior sessions) — committed history reflects prior milestones.
- App boots (`FLASK_CONFIG=production`): PASS.
- Test suite: **92 passed** (`python -m pytest tests -q`, ~166 s).
- Migration heads present:
  `12ee9a1299d2` (baseline) ← `da9c85479549` ← `a131a27bf5e5` ← `d95375ed9481`
  ← `a0b299aeba56` ← `ed88d1bd7e4b` (current).
- Config profiles: development (DEBUG=True), production (DEBUG=False + secure
  cookies + SECRET_KEY validation), testing (in-memory SQLite).
- Production secret: `create_app` **refuses weak/absent SECRET_KEY** at boot
  (RED-gate passed).
- Production/development separation: `db.create_all()` is skipped in
  production; schema owned by Alembic. Division of labour respected.

## Findings log
### RED
- ~~(pending) open-redirect in auth login `next` param~~ **FIXED** — protocol-relative
  and absolute URLs rejected; regression tests added (`tests/test_security_fixes.py`).

### ORANGE
- (none identified yet — Phase 4 authorization deep-dive pending).

### YELLOW
- Patient full name + MRN are transmitted to the external Gemini provider for
  medication review (`app/services/ai/clinical_pharmacist.py` `_build_prompt`).
  PHI minimization: name/MRN are not needed for an MTR.
- ~~Client-supplied `status` accepted when creating prescriptions~~ **FIXED**
  (`app/routes/api.py` `create_prescription` now hardcodes `status='Active'`;
  `create_referral` hardcodes `status='Pending'`). Regression tests verify
  client-injected status values are ignored.

### GREEN
- Public self-registration is **hard-coded to `patient`**; staff roles require
  administrator provisioning. No role-escalation via registration.
- Password hashing: bcrypt. Account lockout (5 attempts / 15 min) present.
- CSRF enabled globally; JSON API validates `X-CSRFToken`/body token on writes.
- Documents stored in private `UPLOAD_FOLDER`; downloads require owner /
  need-to-know; `_document_path` has no user-controlled traversal.
- Reports PDFs gated by `has_need_to_know` (patient-only in reports).
- API object-level access via `_require_patient_access` (self / need-to-know /
  oversight roles); public endpoints expose catalogue data only.
- Cashier role permissions fixed: ROLE_PERMISSIONS entry added with
  PATIENT_VIEW, BILL_VIEW, BILL_EDIT, PAYMENT_RECORD, TIMELINE_VIEW,
  SEARCH_GLOBAL (`app/permissions.py`).

## Fixed findings
1. **Open redirect (RED)** — `app/routes/auth.py`: Added `_safe_next()` helper
   that rejects protocol-relative (`//host`) and absolute (`https://...`)
   URLs. 3 regression tests cover protocol-relative, absolute, and local paths.
2. **Client-injected prescription status (YELLOW)** — `app/routes/api.py` line
   439: `status=data.get('status', 'Active')` → `status='Active'`. 1 regression
   test verifies `'Cancelled'` is rejected.
3. **Client-injected referral status (YELLOW)** — `app/routes/api.py` line 536:
   `status=data.get('status', 'Pending')` → `status='Pending'`. 1 regression
   test verifies `'COMPLETED'` is rejected.
4. **PHI sent to external AI (YELLOW)** — `app/services/ai/clinical_pharmacist.py`:
   Removed patient `full_name` and `MRN` from Gemini prompt; retains age, gender,
   meds, allergies, diagnoses, and lab results (clinically necessary data only).

## Remaining findings
- **YELLOW**: No `SERVER_NAME` or `TRUSTED_HOSTS` config — verify host-header
  protection. (Pending Phase 31 documentation.)
- **YELLOW**: Weak password policy (min 8 chars only, no complexity). (Phase 3.)
- **YELLOW (accepted)**: Departmental worklists (lab orders, pharmacy prescriptions,
  radiology orders, billing bills) return system-wide results within the
  authorized role. This is intentional for hospital departmental workflows —
  lab techs/pharmacists/radiologists/billing staff need to see all items in
  their queue. Role-based access (`@roles_required`) is the primary access
  control; adding patient-level filtering would break clinical workflows.
  Subject to hospital policy review.
- **YELLOW (accepted)**: Admin staff management routes allow any Admin to
  manage any staff member's roles (except SuperAdmin — privilege escalation
  is guarded). This is standard admin oversight behavior.

## Phase 4 — Authorization/RBAC/IDOR deep-dive (151 routes across 14 files)

### RED findings (all resolved)
None remaining. Two critical IDOR issues fixed:

1. **`patient.py` book_appointment IDOR** (line 99-106): A Patient-role user
   could inject a `patient_id` form field to book an appointment for another
   patient via the fallback path. **FIXED** — Patient-role users now always use
   their own profile; the form fallback is restricted to Admin/Receptionist
   roles.

2. **`patient.py` messages route** (line 247): Missing `@roles_required`
   decorator — any authenticated user (including Doctors) could access the
   patient messages template. **FIXED** — added `@roles_required('Patient',
   'Admin', 'SuperAdmin')`.

### YELLOW findings (acknowledged, see "Remaining findings")
- Departmental worklists (lab, pharmacy, radiology, billing) are system-wide
  by design — role-based access is the primary control.
- Admin role can manage other Admins (only SuperAdmin escalation is guarded).
  Standard admin oversight pattern.

### GREEN routes (properly protected)
- **`doctor.py`**: 17 routes — all use `@patient_access_required` (exemplary).
- **`api.py`**: 18 routes — all use `_require_patient_access()` with role+permission
  checks + CSRF on writes.
- **`reports.py`**: 6 routes — all use `_patient_report_access()`.
- **`auth.py`**: 4 routes — open-redirect guard, bcrypt, lockout, patient-only
  registration.
- **`ai.py`**: 9 routes — all clinical routes use `require_patient_access()`.

### Root cause
The codebase has two authorization patterns: `doctor.py`/`api.py` (exemplary —
object-level + role), and departmental modules (role-only). The departmental
pattern is acceptable for worklist views; the patient portal IDOR was the
genuine gap.

## Session 2026-09-02 (phases 2–48 hardening) — new findings & fixes

### RED (fixed)
- **Production ran on SQLite silently** (`config.py` fallback) with only a
  warning. **FIXED** — `create_app('production')` now raises `RuntimeError` if
  `DATABASE_URL` is absent or points to SQLite. Production requires an explicit
  server database. Regression-tested via subprocess
  (`tests/test_production_hardening.py`).

### ORANGE (fixed)
- **Boolean columns nullable** — 13 columns used Python-side `default=` only
  with no `nullable=False`, diverging from intent on PostgreSQL (NULL instead of
  FALSE/TRUE under raw/bulk inserts). **FIXED** — added `nullable=False`
  (`app/models.py`).
- **Missing FK indexes** — high-volume `patient_id` columns on prescriptions,
  lab_orders, radiology_orders, appointments, medical_records, diagnoses,
  referrals, vital_signs, nursing_notes, medication_administrations, care_plans,
  care_teams, tasks, documents, AI recommendations, intake_output lacked indexes,
  causing full scans on per-patient queries. **FIXED** — added `index=True`
  to 20+ columns; applied across the chain ending at head `59f96da6bbf3`
  (which supersedes `6e4b0dc64295`). Payment idempotency unique constraint
  `payments(bill_id, reference)` applied in `12a33945697d`, and clinical
  hot-path FK indexes applied in `59f96da6bbf3`.

### YELLOW (fixed)
- **No rate limiting** — login/register/API endpoints had no throttling; a
  brute-force vector on login. **FIXED** — Flask-Limiter: login 10/min,
  register 5/hr, plus nginx rate-limit zones. `RATELIMIT_ENABLED` configurable;
  disabled in tests, in-memory storage by default.
- **No request logging / correlation IDs** — no per-request log with latency,
  no way to correlate backend logs with the DB audit trail. **FIXED** —
  `app/services/logging.py` adds access logging with `request_id`, honors an
  inbound `X-Request-ID`, and exposes `get_request_id()`.
- **No production-safe error pages** — error responses could be unstructured.
  **FIXED** — error handlers for 400/403/404/409/429/500/503 render HTML error
  templates (both logged-in and logged-out blocks) or JSON for `/api/`; the 500
  handler rolls back the DB session and never leaks internals.

### GREEN / hardening delivered
- `seed.py` refactored: `--roles-only` is the only production-safe mode; demo
  seed is blocked when `FLASK_CONFIG=production`.
- `backup/backup.py` (PostgreSQL `pg_dump` + SQLite copy, checksums, retention,
  `--verify`).
- `scripts/preflight_check.py` deployment gate.
- `deployment/nginx.conf` (TLS, security headers, rate limits, static caching,
  proxy headers), `deployment/.env.production.example`.
- Docs: `DEPLOYMENT_GUIDE.md`, `BACKUP_AND_RECOVERY.md`, `SECURITY_OPERATIONS.md`.

### Test evidence (this session)
- 9 new regression tests in `tests/test_production_hardening.py` (error
  handlers, request-id correlation, production config validation, login rate
  limiting).
- Full run: **101 passed, 0 failures**.
