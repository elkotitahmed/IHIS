# Deploying iHIS to PythonAnywhere

This guide walks a production deployment of iHIS on a single
PythonAnywhere account. PythonAnywhere is a shared-hosting PaaS: one WSGI
process inside a Web session, a MySQL or PostgreSQL backend up to 1&nbsp;GiB, and
no long-running daemons — so iHIS is deployed here as a single Flask app
with background tasks served by in-process workers and DB-driven scheduling.

> **Prerequisites:** a paid PythonAnywhere account (the free tier cannot run
> the async background task workers). A PostgreSQL or MySQL database created in
> the **Databases** tab is required — iHIS refuses SQLite in production.

---

## 1. Reference architecture

```
Browser
   │  HTTPS (PythonAnywhere terminates TLS)
   ▼
PythonAnywhere Web app  (single WSGI process)
   ├── WSGI file ──► from wsgi import application
   ├── gunicorn (2 workers) serving /home/<user>/iHIS_Project
   │   └── Flask app (wsgi.py) ──► create_app('production')
   │       ├── PostgreSQL DB (PythonAnywhere MySQL also supported)
   │       ├── /static → served by PythonAnywhere static file mapping
   │       ├── /var/uploads (private PHI/media) → served by protected routes
   │       ├── in-process background workers (ThreadPoolExecutor)
   │       └── Redis rate-limit storage (optional, recommended)
   └── Scheduled task (daily) → restore-backup / backup jobs
```

---

## 2. Create the database

1. **Databases → MySQL** (or PostgreSQL). Choose `postgresql` for the default
   DB name and user (e.g. `<user>` and `defaultdb` for PostgreSQL, or the
   MySQL `$user$default` encoding).
2. Record the credentials. iHIS reads a full SQLAlchemy URL from `DATABASE_URL`:
   - PostgreSQL: `postgresql+psycopg2://<user>:<pass>@localhost/<db>`
   - MySQL: `mysql+pymysql://<user>:<pass>@localhost/<db>` *(requires adding
     `pymysql` to `requirements.txt`)*
3. Because PythonAnywhere hides real hosts behind a proxy, keep `localhost`
   (or use the `DATABASE_URL` PythonAnywhere displays in the Databases tab).

> PythonAnywhere ships `psycopg2` preinstalled for PostgreSQL; the pinned
> `psycopg2-binary==2.9.10` wheel in `requirements.txt` is used locally. If the
> `pip install` complains about the binary wheel on the server, install the
> system package instead or allow the `psycopg2-binary` build to complete — the
> driver name in `DATABASE_URL` (`postgresql+psycopg2://`) is unchanged either
> way.

---

## 3. Clone the repository

```bash
cd ~
git clone https://github.com/<your-org>/iHIS_Project.git
cd iHIS_Project
```

The repo already includes `requirements.txt`, `wsgi.py`, `migrations/`,
`backup/`, and `scripts/preflight_check.py`.

---

## 4. Create a virtualenv and install dependencies

PythonAnywhere uses **virtualenv** (not venv-style `.venv`). Use the same
major.minor Python the app was tested on (**3.12** — `requirements.txt` is
verified against 3.12.10):

```bash
cd ~/iHIS_Project
python3.12 -m virtualenv venv
source venv/bin/activate
pip install -r requirements.txt
```

Add `mysql+pymysql` support if you chose MySQL:

```bash
pip install pymysql
```

---

## 5. Configure environment variables

iHIS reads configuration from environment variables. Either

**A) Via the Web tab → Environment variables** (recommended, survives deploys),
or

**B) A `.env` file** in the project root (`wsgi.py` loads it with
`python-dotenv`, whose override rule keeps process-env vars authoritative).

Create `.env` from the template:

```bash
cp deployment/.env.production.example .env
```

Then set real values:

```ini
FLASK_CONFIG=production
SECRET_KEY=<64+ hex chars, e.g. `python -c "import secrets;print(secrets.token_hex(32))"`>
DATABASE_URL=postgresql+psycopg2://<user>:<pass>@localhost/<your-db>

# Optional AI medication review
GEMINI_API_KEY=<your Google Gemini key>

# Optional shared rate-limit storage (recommended for >1 web worker)
RATELIMIT_STORAGE_URI=redis://<optional>
```

`production` mode **boots strictly**: a weak/missing `SECRET_KEY` or an
absent/SQLite `DATABASE_URL` raises `RuntimeError` at import time — the app
cannot start insecurely.

---

## 6. Run the database migrations

```bash
cd ~/iHIS_Project
source venv/bin/activate
export FLASK_CONFIG=production
flask db upgrade
```

This applies the Alembic chain to the current head and stamps
`alembic_version`. Verify with the preflight gate:

```bash
python scripts/preflight_check.py
```

Optional: check the chain applies cleanly to an empty scratch DB
(`scripts/_mig_chain_check.py`) before your first real run.

---

## 7. Seed roles, permissions and an admin

```bash
cd ~/iHIS_Project
source venv/bin/activate
export FLASK_CONFIG=production
python seed.py --roles-only      # idempotent; creates roles + permissions only
```

> **Never** run `python seed.py` (the full demo seed) in production — it writes
> demo users/records. `--roles-only` (alias `--roles`) is the only safe seed.

Then create the first admin and assign the created `Admin` role through the
web UI (an existing SuperAdmin) or a one-off CLI script that calls
`app.permissions.seed_permissions()` and assigns roles on a user.

---

## 8. Configure the Web tab

1. **Add a new web app** (“Manual configuration”, Python 3.12 — match the
   interpreter your virtualenv was built with), name
   `<user>.pythonanywhere.com`.
2. **Code → WSGI configuration file** (`/var/www/<user>_pythonanywhere_com_wsgi.py`):

   ```python
   import os, sys
   sys.path.insert(0, '/home/<user>/iHIS_Project')
   from wsgi import application   # noqa: F401
   ```

   `wsgi.py` already exports `application` and defaults to `production`.
3. **Virtualenv** → point at `/home/<user>/iHIS_Project/venv`.
4. **Static files** (mapping):
   - URL `/static/` → `/home/<user>/iHIS_Project/app/static/`
   - Do **NOT** expose `app/static/uploads` or `var/uploads` — private media is
     served only through the access-controlled `ai.ai_media` / document /
     radiology / dentistry routes.
5. **Reload** the app.

> PythonAnywhere’s own HTTP/CSP layer fronts the app. The security headers set
> by `create_app` (`X-Content-Type-Options`, `X-Frame-Options`,
> `Referrer-Policy`, and HSTS in production) remain in effect.

---

## 9. Background tasks & scheduled jobs

iHIS in-process background workers (`app/services/` thread executors) handle
notifications/task-deadline work within the WSGI request lifecycle; long-running
or periodic jobs (preventive reminders, backups) are driven by PythonAnywhere
**Scheduled tasks**:

```
Daily @ e.g. 03:00   → /home/<user>/iHIS_Project/backup/run_backup.sh
```

See `docs/BACKUP_AND_RECOVERY.md` for the backup/restore CLI. Ensure scheduled
tasks run under the same virtualenv and `FLASK_CONFIG=production`.

---

## 10. Health checks & smoke test

- `/health/live` → `200 {"status":"ok"}`
- `/health/ready` → `200` when the DB is reachable, else `503` (no secrets leaked)

Smoke test after deploy:

```bash
curl -i https://<user>.pythonanywhere.com/health/live
curl -i https://<user>.pythonanywhere.com/health/ready
# login, create an appointment, dispense a medication, render a report
```

---

## 11. Operational notes (PythonAnywhere-specific)

- **Single web worker by default.** For concurrency correctness, add an extra
  worker and point `RATELIMIT_STORAGE_URI` at a shared store (Redis).
  Dispensing and payments are protected against double-submit at the app and
  DB level regardless of worker count.
- **No long-running daemons.** Keep heavy scheduled work in Scheduled tasks,
  not inside the web process.
- **Migrations lock the schema.** Run `flask db upgrade` during a brief
  maintenance window, then `touch`/reload the web app.
- **Rate limiting** is on by default (`RATELIMIT_ENABLED=True`, login 10/min,
  register 5/hr) with in-memory storage unless you set `RATELIMIT_STORAGE_URI`.

---

## 12. Rollback

- **Data:** restore the latest verified backup (see
  `docs/BACKUP_AND_RECOVERY.md`). PostgreSQL uses `pg_restore` (the backup is a
  custom-format dump): create an empty DB then
  `pg_restore --no-owner --role=<user> -d <db> backup/backups/ihis_<ts>.sql.gz`
  after the latest `backup/backup.py --verify` run.
- **Schema:** `flask db downgrade <previous-revision>` then re-verify with
  `scripts/preflight_check.py`.
- **Code:** redeploy the last-known-good commit and `touch` the WSGI file to
  trigger a reload.