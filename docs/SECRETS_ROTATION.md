# iHIS Secrets Rotation Procedure

This document describes how to rotate every runtime secret in iHIS. It also
records the secret-hygiene audit result for the release candidate.

## Audit status (2026-09-02)

- `.env` is **untracked** (`git ls-files .env` → empty). Only `.env.example`
  (placeholders) and `deployment/.env.production.example` (placeholders) are
  tracked.
- `.gitignore` ignores `.env` and `.env.*` but keeps `.env.example`.
- Full git history search found **no committed real credentials** (no private
  keys, no `sk-…`, no `AIza…` API keys, no real `postgresql://` passwords).
- **Verdict: ROTATION_NOT_REQUIRED.** No credential rotation is mandated by the
  campaign because nothing real was ever exposed in the repository.

## What to rotate, and when

| Secret | Where used | Generation | Rotate when |
|--------|-----------|------------|-------------|
| `SECRET_KEY` | Flask session/CSRF signing | `python -c "import secrets; print(secrets.token_hex(32))"` | Annually, or on any suspected session forgery |
| `DATABASE_URL` password | PostgreSQL/MySQL connection | any strong password store | On credential compromise |
| `GEMINI_API_KEY` | AI medication review | Google AI Studio | On compromise or key revocation |
| `SMTP_PASSWORD` / OAuth creds | Notifications (if configured) | provider | On compromise |

## Rotation procedure

1. Generate a new value (never reuse an old value).
2. Update it in the environment variable (PythonAnywhere Web tab → Environment
   variables) **or** in `.env` (whichever is authoritative — process env wins
   over `.env` because `wsgi.py` uses `load_dotenv(override=False)`).
3. Reload / restart the app: PythonAnywhere Web tab → **Reload**, or `touch`
   the WSGI file.
4. Verify the app boots: `curl -i /health/live` and `/health/ready` return 200.
5. Rotating `SECRET_KEY` invalidates all existing session cookies — users must
   re-login. Communicate this before an intrusive rotation (e.g. during a
   maintenance window).
6. For `SECRET_KEY`, force a clean break of sessions if needed; the 15-minute
   account-lockout and 24-hour session lifetime remain in force.

## Verification after rotation

- `python scripts/preflight_check.py` → no FAIL.
- `python -m pytest tests -q` → health/auth regression suites still green.
- Confirm production boot rejects a weak/missing `SECRET_KEY` (the
  `create_app('production')` guard turns it into a `RuntimeError`).

## Never

- Print secret values to console, logs, or error pages.
- Commit the real `.env` or any rendered secret into the repository.
- Put backup files containing `.env` into a publicly reachable directory.