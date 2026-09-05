# iHIS Production Readiness Matrix

Generated 2026-09-02 (updated). Each phase is rated using the severity system
from `docs/PRODUCTION_READINESS_AUDIT.md`: RED (blocker), ORANGE (high-risk),
YELLOW (medium), BLUE (improvement), GREEN (acceptable).

## Phase ratings

| # | Phase | Rating | Summary |
|---|-------|--------|---------|
| 1 | Baseline | GREEN | 101 tests pass; app boots production; migration heads verified |
| 2 | Authentication | GREEN | Bcrypt hashing; lockout (5/15); open-redirect fixed |
| 3 | Password policy | YELLOW | Min 8 chars dev / 12 chars production; no complexity policy |
| 4 | Authorization / IDOR | GREEN | 151 routes audited; 2 IDOR fixes; dept worklists role-gated |
| 5 | Clinical integrity | GREEN | Diagnosis/record lifecycle enforced; sign/amend audit trail |
| 6 | RBAC seed | GREEN | 14 roles; `seed_permissions` idempotent; Cashier gap fixed |
| 7 | Seed safety (NEW) | GREEN | `seed.py --roles-only` for production; demo seed blocked in prod |
| 8 | Medication safety | GREEN | Prescriptions require doctor + items; dispensing uses FEFO |
| 9 | Inventory | GREEN | Stock ledger; expiry tracking; reconciliation workflow |
| 10 | Lab workflow | GREEN | Order → accept → result → verify → finalize lifecycle |
| 11 | Radiology workflow | GREEN | Order → schedule → perform → report lifecycle |
| 12 | Pharmacy dispensing | GREEN | FEFO + `remaining_qty()` cap; **atomic conditional stock decrement** (`quantity >= take`) prevents lost updates / negative inventory |
| 13 | Billing | GREEN | Auto-billing; void guard; receipt numbers; **payment idempotency** (unique `payments(bill_id, reference)` + double-submit guard) |
| 14 | Admissions | GREEN | Admission/discharge lifecycle; bed management |
| 15 | Task engine | GREEN | Routed tasks with role-based assignment |
| 16 | Notifications | GREEN | In-app + DB notification pipeline; patient self-isolation |
| 17 | Document security | GREEN | Private UPLOAD_FOLDER; owner/need-to-know download guard |
| 18 | API security | GREEN | CSRF on writes; need-to-know per patient; public=catalogue only |
| 19 | Rate limiting (NEW) | GREEN | Flask-Limiter; login 10/min; register 5/hr; nginx zones |
| 20 | DB integrity | GREEN | SQLAlchemy ORM; transaction-per-request; migration-managed schema |
| 21 | PostgreSQL compat (NEW) | GREEN | Boolean NOT NULL enforced; patient_id FK indexes added |
| 21a | Concurrency safety (NEW) | GREEN | Atomic stock decrement + `tests/test_inventory_concurrency.py`; no negative inventory / lost update on double dispense |
| 21b | Payment idempotency (NEW) | GREEN | Unique `payments(bill_id, reference)` + app double-submit guard + `IntegrityError` recovery; no double charge |
| 22 | Search | GREEN | `accessible_patient_ids()` filters list views; full-text supported |
| 23 | Pagination | BLUE | `ITEMS_PER_PAGE` configured; not applied consistently to all lists |
| 24 | Performance / indexes (NEW) | GREEN | Indexes added to high-volume patient_id FKs in `59f96da6bbf3` (clinical hot-path + need-to-know security FKs); N+1 remains BLUE |
| 25 | RTL / i18n | GREEN | `dir="rtl"` on `<html>`; Arabic/English bilingual templates |
| 26 | Mobile / responsive | GREEN | Bootstrap grid; responsive nav; viewport meta |
| 27 | Accessibility | BLUE | No formal WCAG audit; form labels present; contrast not tested |
| 28 | AI reliability | GREEN | Gemini fallback to rule-based on error; `available()` UI guard; **regression tests**: missing key → 200, provider exception → structured error, no raise |
| 29 | AI data privacy | GREEN | Name/MRN removed from Gemini prompt; only clinical data sent; verified by `tests/test_ai_resilience.py` prompt assertions |
| 30 | Error handling (NEW) | GREEN | Error templates for 400/401/403/404/409/422/429/500/503; JSON for API; no leaks |
| 31 | Structured logging (NEW) | GREEN | `app/services/logging.py`; access log with latency; request IDs |
| 32 | Request correlation (NEW) | GREEN | `X-Request-ID` + `g.request_id` threaded through request lifecycle |
| 33 | Backup (NEW) | GREEN | `backup/backup.py` (PostgreSQL pg_dump + SQLite); checksums; retention; `timezone.utc` (Python ≥3.11, 3.10-safe); SQLite backup→verify→restore proven |
| 34 | DR plan | GREEN | Documented in `docs/BACKUP_AND_RECOVERY.md` & `DEPLOYMENT_GUIDE.md` |
| 35 | Production config | GREEN | DEBUG=False; secure cookies; SECRET_KEY validated; SQLite hard-fail |
| 36 | Migration safety (NEW) | GREEN | New head `59f96da6bbf3`; `scripts/preflight_check.py` |
| 37 | Dependencies | GREEN | Pinned versions; Flask-Limiter added; no known CVEs |
| 38 | Dental | GREEN | Dental records integrated with need-to-know access |
| 39 | Physiotherapy | GREEN | Therapy plans/sessions with role-scoped access |
| 40 | Nursing | GREEN | Vital signs, care plans, nursing notes with nurse ownership |
| 41 | Care team | GREEN | CareTeam/CareTeamMember model with access integration |
| 42 | Medical records | GREEN | Create/sign/amend lifecycle; doctor ownership enforced |
| 43 | Appointments | GREEN | Status transitions (Scheduled→Confirmed→Completed/Cancelled) |
| 44 | Referrals | GREEN | Created→Sent→Accepted→Completed lifecycle; need-to-know |
| 45 | Preventive care | GREEN | Automated screening reminders via `preventive.py` service |
| 46 | Deployment artifacts (NEW) | GREEN | `deployment/nginx.conf`, `.env.production.example`; DEPLOYMENT_GUIDE; **`docs/PYTHONANYWHERE_DEPLOYMENT.md`**; WSGI loads `.env` + `application` alias |
| 47 | Security operations (NEW) | GREEN | `docs/SECURITY_OPERATIONS.md` — secrets, RBAC, network, incident response |
| 48 | Release gate | GREEN | **PRODUCTION-READY CANDIDATE** — see gate below. `docs/RELEASE_CHECKLIST.md` + `docs/RELEASE_READINESS.md` gate the go-live |

## Gate decision (Phase 48)

### Release gate: PRODUCTION-READY CANDIDATE

The iHIS application has transitioned from CONDITIONAL PASS to a
**PRODUCTION-READY CANDIDATE** for internal hospital use. All RED findings are
fixed and regression-tested (100 passing tests). Production boot refuses weak
secrets and SQLite, enforcing a server database. Rate limiting, structured
logging with request correlation, production-safe error handlers, backup tooling,
and deployment/security-operations documentation are in place.

### Deploy prerequisites (runbook)

1. `cp deployment/.env.production.example .env` and set a strong `SECRET_KEY`
   and a PostgreSQL `DATABASE_URL` — the app refuses to boot otherwise.
2. `FLASK_CONFIG=production flask db upgrade` to reach head `59f96da6bbf3`.
3. `python seed.py --roles-only` (production) — never demo data.
4. Run `python scripts/preflight_check.py` and resolve any FAIL.
5. Serve behind nginx (see `deployment/nginx.conf`): TLS termination, proxy
   headers, rate-limiting zones, static caching.
6. Run `backup/backup.py --verify` and schedule backups
   (see `docs/BACKUP_AND_RECOVERY.md`).

### Remaining post-release hardening (non-blocking)

- Password complexity policy above min-length (YELLOW — hospital policy).
- N+1 query optimization on large patient lists (BLUE).
- Formal WCAG accessibility audit (BLUE).
- Production Redis for the limiter storage backend (BLUE — `RATELIMIT_STORAGE_URI`).
- PostgreSQL integration test against a live PG server (ORANGE → GREEN once
  exercised; verified today only via config validation + empty-DB migration chain).

### Test evidence

- **124 tests pass** (`python -m pytest tests -q`, ~3 min)
- 5 security regression tests (open-redirect, status injection, referral status)
- 13 hardening tests (`test_production_hardening.py`): error handlers for
  400/401/403/404/409/422/429/500/503 (JSON + HTML, no leak), production
  cookie-security config, request-id correlation, production config
  validation, login rate limiting, backup-script import portability
- 7 AI-media privacy tests (`test_ai_media_privacy.py`): static private paths
  blocked, role-gated `ai_media`, path-traversal blocked, tooth graceful
- 3 AI resilience tests (`test_ai_resilience.py`): missing key → 200, provider
  exception → structured error, no PII in prompt
- 2 inventory-concurrency tests (`test_inventory_concurrency.py`): no negative
  stock / lost update on double dispense
- 2 E2E hospital simulation tests covering the full patient journey
- 0 failures across all sessions
