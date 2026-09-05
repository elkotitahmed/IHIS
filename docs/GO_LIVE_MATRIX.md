# iHIS Final Go-Live Matrix

Status snapshot for the `release-candidate` (2026-09-02). Every row records the
**evidence** that backs its status and, where relevant, what remains before
go-live. Statuses:

- **GREEN** — verified and acceptable.
- **YELLOW** — no critical blocker; an environment/infra item is not yet live-tested.
- **RED** — a critical unresolved issue (none open).
- **NOT_VERIFIED** — cannot be exercised in this environment (no live
  PostgreSQL / no PythonAnywhere account); must be proven at deployment time.

Test evidence: `python -m pytest tests -q` → **124 passed**, 0 failed.
Migration head: `59f96da6bbf3` (single head, chain applies cleanly to empty DB).

| AREA | STATUS | EVIDENCE | BLOCKER | ACTION | VERIFIED_BY_TEST |
|------|--------|----------|---------|--------|------------------|
| Security | GREEN | Error handlers cover 400/401/403/404/409/422/429/500/503; incremental hardening tests pass | none | none | test_production_hardening |
| Authentication | GREEN | Bcrypt, login lockout (5 tries/15 min), CSRF global, rate limit login 10/min | none | none | test_app, test_advanced, test_production_hardening |
| Authorization | GREEN | RBAC `permissions_required` across 58 routes; need-to-know read boundary | none | none | test_negative_authz, test_advanced, test_security_fixes |
| Clinical Integrity | GREEN | Sign/amend immutable lifecycle; audit old/new snapshots; cross-dept write-blocked | none | none | test_negative_authz, test_clinical_engine |
| Patient 360 | GREEN | Aggregates encounter/dx/rx/lab/radiology/nursing/referral/timeline/billing | none | none | test_hospital_simulation, test_advanced |
| Lab | GREEN | Order→specimen→result→verify→finalize; amendment; critical result workflow | none | none | test_advanced, test_billing |
| Radiology | GREEN | Order→study→report→sign→finalize; image download IDOR-gated | none | none | test_advanced, test_radiology_workflow, test_ai_media_privacy |
| Pharmacy | GREEN | Rx→review→allergy/interaction→dispense; atomic stock decrement | none | none | test_pharmacy_workflow, test_inventory_concurrency, test_advanced |
| Nursing | GREEN | Vitals/note/care-plan/MAR/administration; MAR cannot change source Rx | none | none | test_nursing_workflow, test_advanced |
| Physiotherapy | GREEN | Referral→assessment→plan→session→progress; shows in 360 | none | none | test_advanced (encounters) |
| Dentistry | GREEN | Intake→odontogram→dx→plan→procedure→follow-up; tooth-level history | none | none | test_advanced (encounters) |
| Reception | GREEN | Register/appointment/check-in/queue | none | none | test_billing, test_hospital_simulation |
| Cashier | GREEN | Invoice/payment/receipt; cannot alter clinical data | none | none | test_billing, test_billing_security |
| Billing | GREEN | Idempotent payment (unique bill+reference); double-submit guarded; no overcharge | none | none | test_billing, test_billing_security |
| Tasks | GREEN | Order/referral/critical-result → task with assignee/dept/priority/due/status | none | none | test_tasks, test_task_generation |
| Notifications | GREEN | Lab/radiology final→doctor; critical→urgent; no dup on retry | none | none | test_tasks, test_task_generation |
| Documents | GREEN | Private `var/uploads`; download gated by need-to-know; static blocked | none | none | test_security_fixes, test_ai_media_privacy, test_report_access |
| API | GREEN | JSON error split; request-id; CSRF-token auth; patient-scoped | none | none | test_advanced, test_production_hardening |
| AI | GREEN | Missing-key note (200 not 500); provider-error structured; prompt no PII | none | none | test_ai_resilience, test_ai_access, test_ai_media_privacy |
| Database | YELLOW | Schema proven via empty-DB migration; no live-PG local integration | none | smoke on real PG | — (NRV) |
| Migrations | GREEN | Single head `59f96da6bbf3`; chain applies to empty DB | none | none | scripts/_mig_chain_check.py |
| Backup | YELLOW | `backup.py` PG/SQLite; checksum+retention; SQLite backup/verify/restore proven live | none | run real `pg_dump` backup on PythonAnywhere | manual (SQLite) + test_production_hardening |
| Restore | YELLOW | SQLite restore proven; PG `pg_restore` documented (custom-format) | none | do a real PG restore to scratch DB on PA | manual (SQLite) |
| Files | GREEN | Private PHI distinct from public static; uploads UUID+magic-byte | none | none | test_security_fixes, test_ai_media_privacy |
| Performance | YELLOW | Hot-path FK indexes added (migration); no live latency measurement | none | measure on PA | test inventory/clinical (functionality) |
| Monitoring | YELLOW | `/health/live`, `/health/ready`, request-id, audit logs, structured logs | none | validate PA error.log | test_production_hardening |
| PythonAnywhere | NOT_VERIFIED | Guides written; WSGI `application` verified; no PA account in env | PA account | execute deployment guide §8 | wsgi import check |
| WSGI | GREEN | `wsgi.py` exports `application`, defaults production, loads `.env` | none | none | `python -c "import wsgi"` |
| Static | GREEN | `/static/` serves CSS/JS; `/static/uploads`+`/static/ai_models` 404 | none | none | test_production_hardening |
| Secrets | GREEN | `.env` untracked; git history free of real creds; SECRETS_ROTATION.md | none | none | `git ls-files .env` empty + history scan |
| Rollback | GREEN | Code redeploy; `flask db downgrade`; `pg_restore`/file restore documented | none | none | docs/ROLLBACK |

> `NRV` = not runnable here (no live PostgreSQL/PythonAnywhere). These become
> GREEN only after the deployment smoke test described in
> `docs/PYTHONANYWHERE_DEPLOYMENT.md` and a real `pg_dump`/`pg_restore` round
> trip are completed on the target account.