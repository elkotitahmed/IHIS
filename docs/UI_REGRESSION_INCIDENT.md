# UI Regression Incident

## Symptom

Some navigation destinations were reported as blank or unusable even though
their Flask routes existed. Route existence alone was not treated as proof of
health.

## Baseline and investigation

- Project root: `D:\AI in health care\protoproject\iHIS_Project`
- Active branch: `main`
- The working tree already contained broad clinical and navigation changes; no
  destructive rollback was performed.
- `/health/live`, `/health/ready`, `/home`, and `/auth/login` returned rendered
  responses.
- Role dashboard smoke testing rendered real HTML for Admin, Doctor, Nurse,
  LabTechnician, Radiologist, Pharmacist, Physiotherapist, Dentist,
  Receptionist, Patient, and Cashier.
- All 170 Jinja templates compiled successfully.
- No shared `base.html` syntax or empty content-block defect was found.
- The new page-render and navigation-health unittest suites pass.
- Existing AI smoke/access and clinical/document regression suites pass.
- The available environment does not have `pytest`; equivalent unittest
  coverage was used for the recovery checks.

## Root cause found

The role landing resolver in `app/routes/main.py` had no Cashier branch.
Cashier users therefore fell through to the public home page instead of their
billing dashboard. The Sidebar also had no Cashier finance group, so the user
had no reliable primary workflow.

This was a real navigation regression, not a missing Flask route.

## Fixes

- Added `Cashier -> billing.dashboard` to the role landing resolver.
- Added a deduplicated Finance group for Cashier with billing, invoices, and
  payments destinations.
- Kept server-side role and permission checks unchanged.
- Added rendered page health tests for every seeded portal role.
- Added navigation and unauthorized-access regression tests.
- Preserved all AI applications and their role guards.

## Files changed for this recovery

- `app/routes/main.py`
- `app/__init__.py`
- `tests/test_page_render_health.py`
- `tests/test_navigation_health.py`
- `docs/UI_REGRESSION_INCIDENT.md`
- `docs/NAVIGATION_HEALTH_REPORT.md`

## Regression prevention

The new tests assert status, redirect destination, non-trivial response size,
the shared `<main>` marker, role page markers, and absence of error-page
content. They also verify the Cashier landing path, unique billing menu link,
and direct unauthorized access behavior.

## Remaining limitations

- Browser console/network verification was not available in the current
  command-only validation run; browser verification is therefore marked
  `NOT_VERIFIED` in the navigation report.
- `pytest` is not installed in this environment, so the repository's pytest
  command could not be executed here. Targeted unittest suites were run.
- The development and testing factories boot successfully. Production boot
  correctly rejects the local SQLite configuration by design.
- A role named Radiology Technician is not implemented in the seeded role
  model; Radiologist is the implemented radiology role and was tested.
