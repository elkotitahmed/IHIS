# iHIS Release Checklist

Use this checklist to gate a production `release-candidate` deployment. Every
item must be `PASS` (or a documented `PASS_WITH_LIMITATIONS`) before go-live.

Command home: repository root `/home/<user>/iHIS_Project`. Interpreter: the
project virtualenv’s Python 3.12.

---

## A. Code & tests

- [ ] `python -m pytest tests -q` → **all green** (157 tests at last count).
      Run from the repo root? NO — run **scoped** to the tests dir
      (`python -m pytest tests -q`) because a transitive `conftest.py` under
      `AI apps/AI-Clinical-Pharmacist/` shadows `app.models` when collected
      from the root.
- [ ] Lint/code-style pass (if a configured linter exists).
- [ ] `python -c "import wsgi; assert hasattr(wsgi,'application')"` → WSGI entry
      exports `application` for PythonAnywhere.

## B. Secret & config hygiene

- [ ] `.env` is NOT tracked by git (`git ls-files .env` empty).
- [ ] `.gitignore` covers `.env`, `.env.*`, `!.env.example`, and `var/`.
- [ ] `SECRET_KEY` is a strong random value (`secrets.token_hex(32)`+); only
      `deployment/.env.production.example` contains the placeholder.
- [ ] `DATABASE_URL` points to PostgreSQL/MySQL (iHIS rejects SQLite in
      production; `create_app('production')` raises otherwise).

## C. Migrations

- [ ] Single Alembic head: `scripts/preflight_check.py` reports exactly one.
- [ ] Chain applies cleanly to an **empty** DB (no `db.create_all()`):
      `python scripts/_mig_chain_check.py` → exits 0, current head
      `59f96da6bbf3`.
- [ ] `flask db upgrade` runs under `FLASK_CONFIG=production`.

## D. Boot & health

- [ ] `create_app('production')` boots with valid env (strict validation).
- [ ] `/health/live` → 200; `/health/ready` → 200 when DB up, 503 when down
      (no secrets leaked).

## E. Security posture

- [ ] `/static/uploads/…` and `/static/ai_models/…` are blocked (404) by the
      `before_request` guard; public CSS/JS under `/static/` still serve 200.
- [ ] Private AI media is served only via `ai.ai_media` behind login +
      roles (Radiologist, Doctor, Nurse, Physiotherapist, Dentist, Admin,
      SuperAdmin); patient role and unauthenticated access denied (403).
- [ ] Security headers present: `X-Content-Type-Options`, `X-Frame-Options`,
      `Referrer-Policy` (all modes); HSTS in production.
- [ ] CSRF is enforced globally; login/register are rate-limited.
- [ ] Cross-patient document / radiology / dental image download is gated by
      `has_need_to_know` / `require_patient_access` (IDOR blocked).
- [ ] Editing a signed/finalized clinical record requires a documented reason,
      reopens as Draft, resets the signer and writes an immutable amendment
      audit (no silent falsification).
- [ ] Privilege escalation blocked: only SuperAdmin may grant/revoke
      SuperAdmin; a plain Admin cannot elevate self; registration always
      yields `Patient`.

## F. Data integrity & concurrency (Phases 27–33)

- [ ] Stock dispensing uses conditional atomic decrement; tests
      `tests/test_inventory_concurrency.py` pass (no negative inventory, no
      lost update).
- [ ] Payments carry a unique `payments(bill_id, reference)` constraint and an
      app-level double-submit guard; duplicate reference is rejected
      (no double charge).
- [ ] Payment amount must be > 0 and may not exceed outstanding balance;
      a bill with payments can’t be voided.

## G. AI resilience (Phases 19/20)

- [ ] `tests/test_ai_resilience.py` pass: missing `GEMINI_API_KEY` renders a
      200 friendly page (not 500); provider exception returns a structured
      error; AI prompt contains no name/MRN/PII identifiers.
- [ ] Real (non-demo) AI weights are deployed under `app/static/ai_models/`:
      fracture `best.pt`, tooth `best_unet.keras`, skin
      `resnet50_best.pth` + `efficientnet_b0_best.pth` (binary 2-class, raw
      state_dicts; demo copies backed up under `var/backups_ai_models/`).
      Verify each page renders its model-availability message and that
      `tests/test_ai_pages_smoke.py` (10 routes × 11 accounts) passes.
- [ ] End-to-end AI smoke (per feature): upload a sample image, run the AI
      tool, confirm the result + private media render (skin heatmap, tooth
      mask, fracture annotation).

## H. Backup & recovery

- [ ] `backup/backup.py --verify` green on a fresh scratch DB; restore to the
      current Alembic head succeeds (77+ tables).
- [ ] Offsite/scheduled backup job configured for production.

## I. Production gate

- [ ] `python scripts/preflight_check.py` → all **PASS** (or documented
      WARN/PASS_WITH_LIMITATIONS).
- [ ] Release docs updated:
      `docs/PRODUCTION_READINESS_MATRIX.md`, `docs/RELEASE_READINESS.md`,
      `docs/RELEASE_CANDIDATE.md`, `docs/AI_IMPLEMENTATION_STATUS.md`.

---

## Sign-off

| Role | Name | Date | Status (PASS / PASS_WITH_LIMITATIONS / NOT_VERIFIED) |
|------|------|------|------|
| Technical release owner | | | |
| Clinical safety reviewer | | | |
| Operations / backup | | | |
| Security reviewer | | | |