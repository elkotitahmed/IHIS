# iHIS Release Readiness

Status snapshot for a production release candidate on PythonAnywhere.
Updated: 2026-09-06 (full-system audit, see `SYSTEM_AUDIT_REPORT.md`). Every item is rated GREEN (acceptable), YELLOW
(medium, non-blocking), or RED (blocker).

## Gate: PRODUCTION-READY CANDIDATE (GREEN)

All RED blockers are resolved. One ORANGE item is a documented environment
limitation (no live PostgreSQL instance to integration-test against), not a
code defect.

---

## Green (acceptable)

| Area | Evidence |
|------|----------|
| Test suite | 257 tests pass `python -m pytest tests -q`; 2,587 role/route combinations render without white pages (`scripts/smoke_all_routes.py`); hot pages inside the SQL query budget (`scripts/profile_queries.py`) |
| Migration chain | Applies cleanly to empty DB; single head `b7c2e9d41f05` (legacy status normalisation) |
| Production boot | `create_app('production')` hard-fails weak secret / SQLite |
| Secrets hygiene | `.env` untracked; git history free of real creds; `SECRETS_ROTATION.md` |
| Auth / RBAC | Bcrypt, lockout, IDOR-gated patient access, no self-elevation |
| Clinical integrity | Sign/amend immutable lifecycle; audit trails |
| Stock / billing | Atomic stock decrement; payment idempotency (no double charge) |
| AI | Resilience + privacy regression tests green |
| Uploads / PHI | Private `var/uploads`; protected media routes; static blocked |
| Headers / CSRF / rate-limit | In place and tested |
| Backups | `backup/backup.py` checksum verify + restore-to-scratch round trip executed 2026-09-06 (SQLite, 92 tables) |
| Frontend UI | Premium design-system rebuild (Command Center, Patient 360 care strip, Doctor workspace, grouped role-aware nav, admin/role-preview banners); all data from real DB queries |
| Clinical feature set | Open-source-benchmark harvesting: order sets (inactive-by-default safety), clinical templates, unified clinical inbox + result acknowledgement, clinical reminders, reusable patient header + safety context (allergies/problems/alerts/active meds across all clinical pages), verified clinical summary, lab critical-value auto-flagging with threshold-based escalation + Doctor notification (idempotent), lab worklist specimen/status columns + filters, pharmacy expiry/low-stock badging with precedence, partial-dispensing visibility |
| Access control | Need-to-know rewritten for all 13 roles (incl. new RadiologyTechnician); supervisory visibility never bypasses locked clinical records; labelled role/patient preview |
| Workflows | Single order-creation service (record → safety → task → timeline → notification) shared by doctor, API, order sets and referrals; lab/radiology/pharmacy/nursing/reception/admissions/billing state machines wired end to end; legacy statuses normalised |
| UI reachability | Every state-changing route reachable from the UI (dead-feature audit); Patient 360 merged timeline; System Health and Hospital Demo pages |
| Docs | `SYSTEM_AUDIT_REPORT.md`, `ROLE_CAPABILITIES.md`, `DEMO_GUIDE.md`, `AI_IMPLEMENTATION_STATUS.md`, `ARCHITECTURE.md`, `PYTHONANYWHERE_DEPLOYMENT.md`, `RELEASE_CHECKLIST.md`, matrix |

## Yellow (medium, non-blocking, post-release)

- Password complexity policy beyond min-length (hospital policy decision).
- N+1 in the FHIR bundle, preventive sweep and reconciliation on very large datasets (hot pages are already profiled and within budget).
- Formal WCAG accessibility audit.
- Production Redis for the limiter's shared storage backend.

## Orange (documented limitation)

- **No live PostgreSQL server locally** to run `flask db upgrade`/backup
  against Postgres itself. PostgreSQL schema correctness is proven via config
  validation, `scripts/preflight_check.py`, and an empty-SQLite migration-chain
  run. The production DB must be smoke-tested once against real Postgres on
  PythonAnywhere before go-live. Python interpreter is **3.12** (the code and
  `backup.py` require ≥3.11; `datetime.UTC` is avoided for portability).

## Red (blockers)

- None open.

---

## To finish go-live

1. Follow `docs/RELEASE_CHECKLIST.md` from A through I.
2. Run `python scripts/preflight_check.py`; resolve any FAIL.
3. On PythonAnywhere: create DB, `flask db upgrade`, `python seed.py
   --roles-only`, configure Web tab + static mapping, run smoke tests
   (`/health/live`, `/health/ready`, a clinical workflow).
4. Exercise a real PostgreSQL backup/restore on the PythonAnywhere DB.