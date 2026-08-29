# iHIS — Deployment Guide

This guide covers running iHIS locally, hardening it for production, and the
Cloudflare-native architecture that best fits this Flask/Python application.

> **TL;DR for Cloudflare:** iHIS is a server-rendered Flask app that needs a
> persistent Python runtime (file-based SQLite + uploads, Alembic migrations,
> ReportLab). Cloudflare Workers/Pages cannot host it as-is. The recommended
> architecture is **Cloudflare at the edge** (proxy DNS/CDN/WAF/TLS, optionally
> a Cloudflare Tunnel) in front of a **Python/WSGI host** running the Flask
> app.

---

## 1. Local Setup

Requirements: Python 3.11+.

```bash
git clone https://github.com/<your-org>/IHIS.git
cd IHIS
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # macOS / Linux
```

## 2. Install Dependencies

```bash
pip install -r requirements.txt
```

The file pins Flask 3, SQLAlchemy, Flask-Migrate, Flask-Login, Flask-Bcrypt,
Flask-WTF, Flask-Cors, WTForms, email-validator, python-dotenv, ReportLab, plus
the production WSGI servers `gunicorn` (Linux/macOS) and `waitress` (any OS).

## 3. Environment Variables

```bash
copy .env.example .env     # Windows
cp .env.example .env       # macOS / Linux
```

Edit `.env`:

- `SECRET_KEY` — generate with `python -c "import secrets; print(secrets.token_hex(32))"`.
- `FLASK_CONFIG` — `development` for local work.
- `DATABASE_URL` — leave as SQLite for local dev.

`.env` is gitignored and is never committed. `.env.example` contains
placeholders only.

## 4. Database Initialization

Tables are created automatically when the app starts (`db.create_all()` in the
app factory). For migrations-based workflow:

```bash
flask db upgrade             # applies migrations under migrations/
```

## 5. Seed Process

```bash
python seed.py
```

`seed.py` creates the schema (if needed), role/permission catalog, departments,
specialties, meds, lab tests, imaging types, demo staff, a demo patient with a
full clinical history, plus care, nursing, dental, physiotherapy, notification,
and document demo data. It is safe to re-run — it skips records that already
exist. Demo credentials are printed by the script; change them immediately, and
never use them in production.

## 6. Running Flask (Development)

```bash
python run.py                 # http://localhost:5000
```

`run.py` uses Flask's built-in dev server with debug on — local development
only.

## 7. Running Tests

```bash
python -m unittest discover -s tests -p "test_*.py"
```

Expected result:

```text
Ran 26 tests
OK
```

Tests use an in-memory SQLite database and need no external services.

## 8. Production Considerations

1. **Configuration**
   - `FLASK_CONFIG=production` (see `config.py`).
   - `SECRET_KEY` **must** be set — the app raises `RuntimeError` at startup in
     production if it is missing or still the dev placeholder.
   - `DATABASE_URL` → **PostgreSQL** (`postgresql+psycopg2://user:pass@host:5432/ihis`).
     SQLite holds a single file, so it cannot serve multiple workers/instances
     or scale horizontally.
2. **WSGI server** — run a real server, not the Flask dev server:
   ```bash
   waitress-serve --host 0.0.0.0 --port 8080 wsgi:app
   gunicorn --workers 3 --bind 0.0.0.0:8080 wsgi:app
   ```
   `FLASK_CONFIG` defaults to `production` from `wsgi:app`.
3. **Reverse proxy / TLS** — terminate HTTPS at the edge (Cloudflare) or with
   nginx/Caddy. Production session cookies are `Secure` so they are only sent
   over HTTPS.
4. **Uploads** — files are written to `app/static/uploads/`. On multi-instance
   setups, store uploads in shared/object storage and adjust
   `UPLOAD_FOLDER` / the `save_upload` helper.
5. **Backups** — schedule database backups (and the uploads directory).
   `flask db upgrade` provides schema migrations.
6. **Reverse-proxy headers** — if behind a proxy, make sure
   `X-Forwarded-*` handling is configured so Flask sees the real client IP
   (used by the login-lockout logic).
7. **Hardening checklist**
   - Enforce HTTPS.
   - Keep CSRF enabled (already global) — API write endpoints marked
     `@csrf.exempt` are intentionally limited to the JSON API.
   - Review CORS origins for the host you deploy on.
   - Change every seeded/demo password before go-live.

## 9. GitHub Deployment Workflow

This repository ships a GitHub Actions CI workflow
(`.github/workflows/ci.yml`) that runs on push/PR to `main`:

1. Checkout, set up Python 3.11.
2. `pip install -r requirements.txt`.
3. Run the unit suite.
4. Boot the app under the `testing` config.

From there you can extend the workflow to **build + push a container** to a
registry and deploy to your chosen platform.

Recommended pipeline:

1. Push to GitHub (`main`).
2. CI validates tests / build.
3. Build an OCI image (e.g. `Dockerfile`) and push to a registry
   (GitHub Container Registry, Docker Hub) — or use a PaaS git integration.
4. Deploy the image to a Python host (see next section).
5. Point your domain at the host through Cloudflare.

## 10. Cloudflare Deployment Architecture

### Cloudflare Python/Flask compatibility — the short version

Cloudflare now supports **Python in Workers (open beta)**, and has a Flask
adapter (`workers.wsgi`) for simple Flask apps. However, iHIS's actual runtime
needs do **not** fit Workers/Pyodide today:

| iHIS dependency                       | Problem on Workers/Pyodide                              |
| ------------------------------------- | ------------------------------------------------------- |
| File-based SQLite (`database/ihis.db`) | No persistent local filesystem; would need D1 or a DO migration |
| Uploaded files on disk (`app/static/uploads/`) | No local disk; would need R2 + code changes        |
| Flask-Migrate / Alembic               | Run-time DB schema tooling, not available in the sandbox |
| ReportLab (PDF generation)            | Heavy native lib; unstable/intermittent in Pyodide      |
| Server-side session + bcrypt + WSGI extras | Works partially, but sustained sessions/multi-process semantics differ |

Conclusion: **do not convert iHIS to a Worker.** Keep the Flask app intact and
put Cloudflare in front of it.

### Recommended architecture (A) — Cloudflare + external Python host

```
                 Users
                   │
      ┌────────────▼────────────┐
      │      Cloudflare         │   proxy DNS, CDN cache, WAF, DDoS, TLS
      │  (zone + CDN + WAF)     │   free plan is sufficient to start
      └────────────┬────────────┘
                   │  (A1) public DNS  →  PaaS/VPS origin
                   └────────────┬────────────┘
                                │
                   ┌────────────▼──────────────────────────┐
                   │  Python host (Waitress/Gunicorn + wsgi:app)
                   │  Fly.io · Railway · Render · Azure · VPS
                   │  PostgreSQL + shared object storage
                   └───────────────────────────────────────┘
```

- Add the host's public IP/hostname as an **A / AAAA / CNAME** record in
  Cloudflare with proxy mode **on** (orange cloud).
- Enable Cloudflare TLS/Full (Strict) and a WAF/managed rules baseline.
- Cache static assets (`/static/*`) at the Cloudflare CDN.

### Recommended architecture (B) — Cloudflare Tunnel (no public IP)

```
   Cloudflare (zone) ◄──cloudflared tunnel──► server on your network / VPS
```

- Install `cloudflared` on the Flask host and run a raw TCP or HTTP tunnel to
  `localhost:8080`. The host never needs a public IP.
- Domain remains proxied by Cloudflare; TLS terminates at Cloudflare, the
  tunnel carries traffic privately.

### What about Workers/Pages?

- **Cloudflare Pages** — hosts static sites & Pages Functions (Node/Python
  functions). It cannot run a long-lived Flask app, file system, SQLite, or
  WSGI server.
- **Workers (Python)** — can host a *simple* Flask app via the beta WSGI
  shim, but iHIS's filesystem/DB/report-lab requirements make it a porting
  project, not a deployment. If you ever want to explore it, the path is:
  D1 (SQL) + R2 (uploads) + removing ReportLab/Alembic — a significant
  rewrite outside the scope of this guide.

## 11. Cloudflare Limitations That Apply to Flask/Python

1. **No persistent filesystem or long-lived processes.** WSGI servers,
   on-disk SQLite, and local upload storage do not exist in the Workers
   sandbox.
2. **Python Workers is in beta.** Package support (e.g. `reportlab`) is not
   guaranteed; native/multi-threaded behavior is restricted.
3. **Pages is not a Python app server.** It serves static assets and
   Functions only.
4. **Session semantics.** Flask sessions (signed cookies) work, but rely on
   `SECRET_KEY` being stable; store it in a Cloudflare Secret / env var.
5. **Your origin must be reachable privately or publicly** for Tunnel/proxy
   modes; keep `DJANGO`/`FLASK_CONFIG` production and CSRF on everywhere.

## 12. Deploy on Render (auto-deploy from GitHub) + Cloudflare

The repository is already "host-ready": it ships a `Procfile`
(`web: gunicorn --bind 0.0.0.0:$PORT wsgi:app`), a `runtime.txt`, the
`psycopg2-binary` driver, and `create_app()` creates the schema automatically
on boot via `db.create_all()` — no CLI commands needed to get running.

### 12.1 Create the service on Render

1. Go to https://render.com and sign up using **Connect with GitHub**.
2. Dashboard → **New** → **Web Service** → choose the `IHIS` repository
   (authorize the Render GitHub app on first use).
3. Render auto-detects Python. Configure:
   - **Name:** `ihis`
   - **Build Command:** `pip install -r requirements.txt` (default)
   - **Start Command:** `gunicorn --bind 0.0.0.0:$PORT wsgi:app`
   - **Instance Type:** Free (spins down when idle; sufficient for a demo)
4. Add these **Environment Variables**:
   - `SECRET_KEY` → a long random 64-char hex string
   - `FLASK_CONFIG` → `production`
   - (recommended) `DATABASE_URL` → the **Internal Database URL** of a free
     Render PostgreSQL instance (Dashboard → **New** → **PostgreSQL**).
     Without it the app falls back to SQLite, whose file lives on Render's
     ephemeral disk and resets on redeploy.
5. (optional, once) **Advanced → Pre-deploy Command:** `python seed.py` to load
   the demo accounts (password `123456`). Safe to run repeatedly.
6. **Create Web Service** → wait for the deploy → open `https://ihis.onrender.com`.
7. From now on, **every `git push` to GitHub auto-redeploys** the app.

### 12.2 Put Cloudflare in front

1. Add your domain to Cloudflare (a free plan is fine); update your registrar
   Nameservers to the ones Cloudflare shows you.
2. **DNS → Add record:**
   - Type: `CNAME`, Name: `ihis`, Target: `ihis.onrender.com`
   - Proxy status: **Proxied** (orange cloud)
3. **SSL/TLS → Overview → Mode:** `Full (Strict)`.
4. Open https://ihis.yourdomain.com — iHIS is now served through Cloudflare
   (HTTPS, CDN, WAF) with the Flask app running on Render.
5. Optional: in **Caching → Cache Rules**, add a rule to cache
   `ihis.yourdomain.com/static/*` at the edge.

### 12.3 Updating the app later

```bash
git add -A
git commit -m "describe the change"
git push origin main        # Render redeploys automatically
```

### 12.4 Alternatives to Render

- **Railway** — same idea; Start Command:
  `gunicorn --bind 0.0.0.0:$PORT wsgi:app`, plus `DATABASE_URL` for a Railway
  PostgreSQL.
- **Fly.io** — `fly launch` → set `cmd = "gunicorn -b :8080 wsgi:app"`,
  internal port 8080, attach a Fly Postgres.
- **VPS (Ubuntu)** — `git clone`, venv, `pip install -r requirements.txt`,
  run `wsgi:app` under Gunicorn + systemd, keep Cloudflare as the front and/or
  add a Cloudflare Tunnel (section 10B).