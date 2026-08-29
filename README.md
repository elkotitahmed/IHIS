# iHIS — Integrated Health Information System

A multi-portal hospital information system built with **Flask** that brings an
entire healthcare facility onto one platform: patient, doctor, nursing,
reception, laboratory, radiology, pharmacy, dentistry, physiotherapy, admin,
and super-admin portals — with an in-app AI clinical-decision-support layer,
care-coordination modules, reporting/PDF generation, and a JSON REST API.

> **Deployment note:** iHIS is a server-rendered Flask application. For
> Cloudflare deployment details, limitations, and the recommended architecture
> (Cloudflare at the edge in front of a Flask/Python host), see
> [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Table of Contents

- [Features](#features)
- [Technology Stack](#technology-stack)
- [Architecture](#architecture)
- [Authentication & RBAC](#authentication--rbac)
- [AI Layer](#ai-layer)
- [REST API](#rest-api)
- [Care Coordination & Reports](#care-coordination--reports)
- [Local Development](#local-development)
- [Environment Variables](#environment-variables)
- [Production Configuration](#production-configuration)
- [Testing](#testing)
- [Security](#security)
- [Deployment](#deployment)
- [Repository Structure](#repository-structure)

---

## Features

- **Multi-portal dashboards** — role-based home pages for every department:
  Patient, Doctor, Nurse, Reception, Laboratory, Radiology, Pharmacy,
  Dentistry, Physiotherapy, Admin, and Super Admin.
- **Clinical workflows** — EMR records, diagnoses (ICD-10), prescriptions,
  lab orders/results, radiology orders/reports, nursing vitals/notes/care
  plans, dental charts/records, physiotherapy assessments/sessions/progress.
- **Appointments & queue** — booking, scheduling, and reception queue.
- **Inventory & pharmacy** — medication catalog and stock management.
- **Messaging & notifications** — in-app messages between patients and staff,
  a global notification center with unread badges and "mark all read".
- **Patient documents** — secure upload/download of medical documents.
- **AI clinical decision support** — rule-based assistants for patient
  summaries, diagnosis support, lab/radiology interpretation, and health
  insights (see [AI Layer](#ai-layer)).
- **Care coordination** — patient referrals, multidisciplinary care teams,
  and complex case management.
- **Reporting** — role-gated HTML dashboard plus **PDF** generation for
  medical records, lab results, radiology reports, and prescriptions.
- **Security** — login lockout, CSRF protection, bcrypt password hashing,
  activity audit logging, and a fine-grained permission system.
- **Testing & CI** — 26 unit tests and a GitHub Actions CI workflow.

## Technology Stack

| Layer        | Technology                                            |
| ------------ | ----------------------------------------------------- |
| Backend      | Python 3.11+, Flask 3                                 |
| Data         | SQLAlchemy ORM, Flask-SQLAlchemy, Flask-Migrate (Alembic), SQLite (dev) / PostgreSQL (prod) |
| Auth         | Flask-Login, Flask-Bcrypt, Flask-WTF (CSRF)           |
| Frontend     | Jinja2 templates, Bootstrap 5, Font Awesome           |
| Reporting    | ReportLab (PDF)                                       |
| CORS         | Flask-Cors                                            |
| Deploy       | Waitress / Gunicorn (WSGI), Cloudflare at the edge    |

## Architecture

iHIS is a classic server-rendered MVC application. The `app/` package hosts all
blueprints, models, templates, and services. Detailed diagrams (layers,
blueprints, AI pipeline, deployment topology) are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Authentication & RBAC

- Sessions via Flask-Login; passwords hashed with bcrypt.
- **Login lockout:** after 5 failed attempts an account is locked for 15
  minutes (`MAX_LOGIN_ATTEMPTS`, `LOCKOUT_MINUTES`).
- **Rules-based RBAC:** portal routes are guarded with `roles_required` /
  `roles_any`, and super-admin functions additionally check fine-grained
  permissions via `permissions_required`.
- All sensitive actions are recorded in an audit log.

Portals/users: SuperAdmin, Admin, Doctor, Patient, Nurse, Receptionist,
LabTechnician, Radiologist, Pharmacist, Dentist, Physiotherapist.

## AI Layer

`app/services/ai/ai_interfaces.py` exposes `AIClinicalAssistant` and related
assistants that produce deterministic, rule-based clinical insights from data
already stored in iHIS. They act as drop-in points so a real ML model or
external LLM endpoint can replace the heuristics later **without changing the
calling code**. Routes live under `/ai/...` (e.g. patient summaries, diagnosis
support, lab/radiology interpretation, analytics, health insights for
patients/admin).

## REST API

A JSON API is mounted at `/api` (e.g. `/api/health`, `/api/doctors`,
`/api/patients/<id>/records`, `/api/prescriptions`, `/api/appointments`,
`/api/inventory`, `/api/users/me`). List endpoints are scoped to the caller's
role (patients see only their own data). Full reference:
[docs/API.md](docs/API.md).

## Care Coordination & Reports

- **Care:** referrals, care teams, and multidisciplinary cases under `/care`
  (clinical/admin portals only; patients are denied).
- **Reports:** `/reports/` dashboard is role-gated; PDF download links are
  generated with ReportLab.

## Local Development

```bash
# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
copy .env.example .env         # Windows
# cp .env.example .env         # macOS/Linux
#  - Edit .env and set SECRET_KEY (use a long random value)

# 4. Initialize the database (tables are auto-created on first run too)
python seed.py                 # creates the schema and demo data

# 5. Run the development server
python run.py                  # http://localhost:5000
```

> Demo accounts are created by `seed.py`; see the seed script for account
> names/emails. Do **not** use demo credentials in production.

## Environment Variables

| Variable        | Required | Default                          | Description                                |
| --------------- | -------- | -------------------------------- | ------------------------------------------ |
| `SECRET_KEY`    | Prod     | dev-only placeholder             | Flask session signing key (long & random!). |
| `FLASK_CONFIG`  | No       | `development`                    | `development` / `production` / `testing`    |
| `DATABASE_URL`  | No       | `sqlite:///database/ihis.db`     | SQLAlchemy connection string. Use PostgreSQL in production. |
| `PORT`          | No       | `5000`                           | Port used by the WSGI entry point.         |

All values go in `.env` (never committed) or the deployment host's real
environment. Copy `.env.example` for the full list with placeholders.

## Production Configuration

- Set `FLASK_CONFIG=production`.
- **`SECRET_KEY` is mandatory** — the app refuses to start in production
  without it (raises `RuntimeError`). Generate one with:
  `python -c "import secrets; print(secrets.token_hex(32))"`
- Use `DATABASE_URL` pointing to a managed PostgreSQL (required for multiple
  workers; SQLite is a single-file, single-host store).
- Run behind a WSGI server on a secure origin:
  ```bash
  waitress-serve --host 0.0.0.0 --port 8080 wsgi:app          # any OS
  gunicorn --bind 0.0.0.0:8080 wsgi:app                       # Linux/macOS
  ```
- Production session cookies are `HttpOnly`, `Secure`, and `SameSite=Lax`.
- **CSRF stays enabled** for all web forms; API write endpoints that
  intentionally use `@csrf.exempt` are unchanged by design.

## Testing

```bash
python -m unittest discover -s tests -p "test_*.py"
```

The suite covers portal RBAC, auth/lockout, the AI layer, care coordination,
PDF reports, the REST API, profile/password changes, patient documents, and
notifications (26 tests). CI runs the same suite in
`.github/workflows/ci.yml`.

## Security

- Secrets are **never** committed — `.env` and `.vscode/` (which may contain
  API keys) are gitignored. Provide secrets via environment variables.
- CSRF protection enabled globally (Flask-WTF).
- Passwords bcrypt-hashed; login lockout against brute force.
- Audit log of sensitive operations; role + permission gating on all portals.
- Treat iHIS as pre-production: review cookie/header policies and enforce
  HTTPS at the edge before serving real patient data.

## Deployment

See **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for:

1. Local setup and running Flask
2. Database initialization and seeding
3. Production considerations
4. GitHub → deployment workflow
5. **Cloudflare deployment architecture** (recommended: Cloudflare edge/CDN +
   Cloudflare Tunnel in front of a Python/WSGI host) and the Cloudflare
   limitations that apply to Flask/Python apps.

### Cloudflare in one paragraph

iHIS is a Python/Flask app that depends on a persistent file system (SQLite
database + uploaded files), Alembic migrations, and ReportLab — things a
serverless Workers/Pyodide runtime cannot fully provide today. The right
pattern is **Cloudflare in front** (proxy DNS, CDN, WAF, TLS, and optionally a
Cloudflare Tunnel) **plus a real Python host behind it** (a VPS, container, or
a PaaS like Fly.io / Railway / Render / Azure). Details and alternatives are in
the deployment guide.

## Repository Structure

```
iHIS/
├── app/
│   ├── __init__.py          # app factory, blueprints, context processors
│   ├── models.py            # all ORM models (60+ entities)
│   ├── forms.py             # WTForms definitions
│   ├── routes/              # one blueprint per portal + api + ai + care + reports
│   ├── services/            # AI, lab, pharmacy, radiology, reports services
│   ├── templates/           # Jinja2 templates grouped by portal
│   └── static/              # CSS (uploads are gitignored)
├── tests/                   # test_app.py, test_advanced.py
├── migrations/              # Alembic migration scripts
├── docs/                    # ARCHITECTURE.md, API.md, DEPLOYMENT.md
├── .github/workflows/        # GitHub Actions CI
├── config.py                # environment-driven configuration
├── run.py                   # development runner
├── wsgi.py                  # production WSGI entry point
├── seed.py                  # schema + demo data seeder
└── requirements.txt         # pinned runtime dependencies
```