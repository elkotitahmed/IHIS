# iHIS Release Candidate

**Candidate:** `release-candidate` (working branch of the production-readiness
effort, 2026-09-02; clinical benchmark cycle 2026-09-05; full-system audit 2026-09-06)
**Target platform:** PythonAnywhere (single WSGI web app + PostgreSQL/MySQL)
**Gate status:** PRODUCTION-READY CANDIDATE

---

## What is in this candidate

A hardened release of the iHIS Hospital Information System that is safe to
deploy for internal hospital use. This is **not a rewrite** — it is the existing
system audited, verified, and strengthened for production.

### Fixes and hardening delivered

- **Private AI media PHI fix** — fracture-detection and tooth-segmentation
  uploads/results moved out of `static/uploads` into private `var/uploads`,
  served only through a login + role-gated `ai.ai_media` endpoint; any
  `/static/uploads/…` or `/static/ai_models/…` request is 404’d.
- **Strict production boot** — `create_app('production')` refuses a weak/missing
  `SECRET_KEY` and any non-PostgreSQL/non-MySQL `DATABASE_URL`.
- **Security headers** — `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy` (all modes) and HSTS (production).
- **Rate limiting** — login 10/min, register 5/hr via Flask-Limiter.
- **CSRF** enforced globally.
- **Concurrency-safe dispensing** — stock decrement is atomic and
  conditional (`quantity >= take`), preventing lost updates / negative inventory.
- **Payment idempotency** — unique `payments(bill_id, reference)` constraint +
  double-submit guard + graceful `IntegrityError` recovery.
- **AI resilience** — medication review never raises on missing key / provider
  error / timeout; structured error, friendly 200. AI prompt carries no
  name/MRN/PII.
- **Performance** — indexes added across clinical hot-path and need-to-know
  FK columns (migration `59f96da6bbf3`).
- **Backups (portable)** — `backup/backup.py` (PostgreSQL pg_dump + SQLite),
  checksums, retention; uses `timezone.utc` (Python ≥3.11, also 3.10-safe);
  SQLite backup→verify→restore proven locally.
- **Error handling & logging** — JSON/HTML error split over 400/401/403/404/
  409/422/429/500/503, request-ID correlation.
- **Deployment tooling** — `scripts/preflight_check.py` release gate,
  `docs/PYTHONANYWHERE_DEPLOYMENT.md`, `docs/RELEASE_CHECKLIST.md`,
  `docs/RELEASE_READINESS.md`, `docs/PRODUCTION_READINESS_MATRIX.md`.
- **Clinical feature harvesting (2026-09-05)** — reimplemented high-value
  reference-EHR features natively (OpenMRS/Bahmni/OpenEMR/fhir-ui benchmark;
  licence audit in `docs/OPEN_SOURCE_ATTRIBUTIONS.md`, no code copied):
  order sets (inactive-by-default safety, applying materialises real orders +
  tasks), clinical templates, unified clinical inbox + idempotent result
  acknowledgement, clinical reminder engine (dedup + auto-close), a reusable
  **smart patient header + safety context** (`PatientSafetyContext` +
  `_patient_header.html`) surfacing allergies / problems / open alerts / active
  meds on every single-patient clinical page across doctor, AI, pharmacy,
  nursing and dentistry, a verified clinical summary (with care team + printable
  view), **lab critical-value auto-flagging** (per-test thresholds, escalation +
  idempotent Doctor alert, re-flagged at verification), a **lab worklist** with
  specimen/status columns and filters, **pharmacy inventory badging**
  (expired/low-stock/expiring with precedence), **partial-dispensing visibility**,
  **reference ranges inline on every lab result view**, a **scheduling recall
  board** (follow-ups + reminders bucketed Overdue / Due Today / Upcoming with
  one-click acknowledgement) and an **in-patient dashboard widget** (per-ward
  census with occupancy bars + a Needs Attention board for overstays and open
  high/critical alerts), plus a **read-only FHIR R4 feed** (`/fhir`:
  `Patient` resources, per-patient `$everything` Bundle, browser view) so the
  structured record is interoperable with standard FHIR consumers.
  Migrations `f1b1a6907bef` (5 clinical tables) and `8f3c0d1a2e9b`
  (critical thresholds).

- **Full-system audit (2026-09-06)** — see `docs/SYSTEM_AUDIT_REPORT.md`:
  need-to-know access rewritten for all roles (new RadiologyTechnician role),
  one shared order-creation service, referral/lab/radiology/pharmacy/nursing/
  reception/admissions/billing state machines wired end to end with tasks,
  notifications and timeline events, Patient 360 merged timeline, System
  Health and Hospital Demo pages, legacy status normalisation migration
  `b7c2e9d41f05`, Clinical Inbox N+1 removed, global-search crash fixed,
  every state-changing route reachable from the UI, CI extended with the
  migration-chain check, seeded route smoke and query-budget profile.

## Verified by

- **257 automated tests pass** (`python -m pytest tests -q`), no regressions;
  2,587 role/route combinations render without white pages.
- Full DB migration chain applies cleanly to an empty database; single Alembic
  head `b7c2e9d41f05`.
- Backup verify + restore to a scratch DB (92 tables) succeeds (SQLite, 2026-09-06).
- WSGI exports `application`; production import + boot validated.

## Known limitations (non-blocking)

- Live PostgreSQL integration not run locally (no PG server); must be
  smoke-tested once against the real PythonAnywhere DB.
- Password complexity policy, N+1 queries, WCAG audit, and a shared
  rate-limit store remain as post-release hardening (YELLOW/BLUE).

## Go-live path

Follow `docs/RELEASE_CHECKLIST.md` → `docs/PYTHONANYWHERE_DEPLOYMENT.md`. The
preflight gate must be GREEN before promotion to production.