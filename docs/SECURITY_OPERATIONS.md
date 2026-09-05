# iHIS Security Operations Guide

## Secrets Management

### Required Secrets
| Secret | Purpose | Min Length | Rotation |
|--------|---------|------------|----------|
| SECRET_KEY | Flask session signing | 64 chars (hex) | Annually |
| DATABASE_URL | PostgreSQL connection | N/A | On compromise |
| GEMINI_API_KEY | AI medication review | 39 chars | On compromise |

### Secrets Handling Rules
1. **NEVER** commit secrets to version control
2. **NEVER** log secrets in application output
3. **NEVER** print secrets in error messages
4. **ALWAYS** use environment variables or a secrets manager
5. **ALWAYS** validate secrets at boot (fail fast if missing)

### Secrets Rotation Procedure
1. Generate new secret: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Update environment variable
3. Restart application
4. Verify application boots correctly
5. Old sessions will be invalidated (users must re-login)

## Authentication Security

### Password Policy
- Production minimum: 12 characters
- Development minimum: 8 characters
- Enforced at registration and password change

### Account Lockout
- Trigger: 5 failed login attempts
- Duration: 15 minutes
- Scope: per-email address
- Reset: automatic after lockout period

### Session Management
- Cookie flags: Secure, HttpOnly, SameSite=Lax
- Session lifetime: 24 hours
- Remember-me: 7 days (with Secure + HttpOnly)
- CSRF protection: enabled globally for all forms

### API Security
- CSRF token required on all state-changing API requests
- Token passed via X-CSRFToken header or JSON body
- Patient-scoped access control on all clinical endpoints

## Authorization Model

### Role-Based Access Control (RBAC)
- 12 roles: SuperAdmin, Admin, Doctor, Patient, Nurse, LabTechnician, Radiologist, Pharmacist, Receptionist, Dentist, Physiotherapist, Cashier
- Permissions are granular: {resource}_{action} (e.g., LAB_RESULT_CREATE)
- Role-permission mapping seeded via `seed.py --roles-only`

### Object-Level Access Control (Need-to-Know)
- Staff access patient records only through documented relationships
- Care team membership, authored documents, or explicit appointments
- Admin/SuperAdmin retain oversight access
- Patient users access only their own record

## Network Security

### TLS/HTTPS
- Required in production
- Minimum: TLS 1.2
- HSTS: max-age=31536000; includeSubDomains

### Security Headers (set in-app via `after_request`)
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` (production
  only)
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN`
- `Referrer-Policy: strict-origin-when-cross-origin`

PythonAnywhere fronts the app with its own HTTP/TLS layer; the headers above
are added by the application regardless of the reverse proxy.

### Rate Limiting (Flask-Limiter)
- Login (`app/routes/auth.py`): **10 per minute** per IP
- Registration: **5 per hour** per IP
- Storage defaults to in-memory; points to `RATELIMIT_STORAGE_URI` (e.g. Redis)
  when more than one web worker is used so limits are shared cluster-wide.
- Controlled by `RATELIMIT_ENABLED` (default on in production).

## Monitoring

### Health Checks
- Liveness: `/health/live` (process is running)
- Readiness: `/health/ready` (database is reachable)

### Audit Logging
- All state-changing operations logged to AuditLog table
- Captures: user_id, action, resource, resource_id, IP, user-agent
- Clinical amendments include old_value/new_value snapshots

### Alert Triggers
- 5xx error rate > 1% in 5 minutes
- Health check failures for > 2 minutes
- Database connection pool exhaustion
- Failed login rate spike (> 20/min)
- Disk usage > 80%

## Incident Response

### Data Breach
1. Contain: isolate affected systems
2. Assess: determine scope of PHI exposure
3. Notify: hospital administration and affected patients
4. Remediate: fix vulnerability, rotate credentials
5. Document: complete incident report

### System Outage
1. Detect: health check failure or user reports
2. Diagnose: check logs, database, disk space
3. Recover: restart services or restore from backup
4. Verify: run smoke tests
5. Communicate: notify affected users
