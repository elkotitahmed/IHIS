# Navigation Health Report

## Legend

- **PASS**: destination rendered verified content and access was appropriate.
- **FAIL**: confirmed defect requiring repair.
- **BLOCKED**: validation could not run in this environment.
- **NOT_VERIFIED**: requires browser or production data verification.

## Role landing matrix

| Role | Primary destination | HTTP | Rendered content | Result |
|---|---|---:|---|---|
| SuperAdmin | `/admin/dashboard` | 200 | Yes | PASS |
| Admin | `/admin/dashboard` | 200 | Yes | PASS |
| Doctor | `/doctor/dashboard` | 200 | Yes | PASS |
| Nurse | `/nursing/dashboard` | 200 | Yes | PASS |
| LabTechnician | `/lab/dashboard` | 200 | Yes | PASS |
| Radiologist | `/radiology/dashboard` | 200 | Yes | PASS |
| Pharmacist | `/pharmacy/dashboard` | 200 | Yes | PASS |
| Physiotherapist | `/physiotherapy/dashboard` | 200 | Yes | PASS |
| Dentist | `/dentistry/dashboard` | 200 | Yes | PASS |
| Receptionist | `/reception/dashboard` | 200 | Yes | PASS |
| Cashier | `/billing/dashboard` | 200 | Yes | PASS |
| Patient | `/patient/dashboard` | 200 | Yes | PASS |

## Shared and clinical pages

| Area | Verification | Result |
|---|---|---|
| Public home and login | Flask test client, non-empty HTML | PASS |
| Health live/readiness | JSON response and database check | PASS |
| Shared `base.html` | 170 templates compiled | PASS |
| Patient 360 / clinical summary | Existing targeted tests | PASS |
| AI zero-argument pages | Role smoke matrix | PASS |
| AI patient/document flows | Existing targeted tests | PASS |
| Sidebar route inventory | 53 configured links matched to routes | PASS |
| Browser console and network | Playwright role/link smoke and asset checks | PASS |

## Navigation repair

| Role | Section | Menu | Endpoint | Result |
|---|---|---|---|---|
| Cashier | Finance | Billing | `/billing/dashboard` | PASS |
| Cashier | Finance | Invoices | `/billing/bills` | PASS |
| Cashier | Finance | Payments/Reports | `/billing/reports` | PASS |
| Clinical roles | Clinical | Workbench and clinical workflows | `/clinical` and related routes | PASS |
| Authorized clinical roles | AI Tools | Image and clinical AI | `/ai/...` | PASS |

Duplicate exact URLs are removed by the centralized menu compaction step.
Distinct AI applications remain separate.

## Access-control checks

- Cashier cannot access Admin or Doctor patient management.
- Cashier can view the clinical workbench through the existing
  `TIMELINE_VIEW` permission but cannot perform protected clinical edits.
- AI route role guards remained green in the existing AI smoke/access suites.

## Test commands

- `python -m unittest discover -s tests -p 'test_page_render_health.py' -q`
- `python -m unittest discover -s tests -p 'test_navigation_health.py' -q`
- `python -m unittest discover -s tests -p 'test_ai_pages_smoke.py' -q`
- `python -m unittest discover -s tests -p 'test_ai_access.py' -q`
- `python -m unittest discover -s tests -p 'test_clinical_summary.py' -q`
- `python -m unittest discover -s tests -p 'test_ai_document_flow.py' -q`

The repository uses `unittest` discovery for the full suite. The complete run
passes: **232 tests in 392.971 seconds**. The run emits extensive per-request
logging, but completed with `OK`.

## Final browser validation

Playwright browser validation is now recorded in
`docs/BROWSER_REGRESSION_REPORT.md`. Major seeded roles were logged in and
their visible sidebar destinations were opened in a real browser. The earlier
Nurse/Lab 403 menu regression and the patient-document 500 were reproduced and
fixed. Console and network diagnostics were clean for the tested destinations.
