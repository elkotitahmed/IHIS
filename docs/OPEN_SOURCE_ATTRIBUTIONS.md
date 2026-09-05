# OPEN-SOURCE ATTRIBUTIONS & LICENSING AUDIT

This file records the licensing review performed while benchmarking iHIS against
open-source healthcare reference systems, and the resulting code-reuse decisions.

**Policy:** iHIS is an original work. The referenced projects below were studied as
*reference systems* (feature/behavior/pattern research). **No source code from any of
them is copied, adapted, translated, or vendored into iHIS.** All reimplementations are
native (Flask/Jinja/SQLAlchemy) and original. Therefore no copyleft obligations attach to
iHIS.

## Audit table

| Reference project | Published license | Source & verification | Compatibility risk | Decision |
|-------------------|-------------------|----------------------|--------------------|----------|
| OpenMRS (`openmrs/openmrs-core`) | MPL-2.0 | MPL-2.0 — widely documented | MPL-2.0 is file-level copyleft | No code copied. Patterns reimplemented clean-room. |
| OpenMRS ESM chart (`openmrs-esm-patient-chart`) | Shows "Other" on GitHub UI; repo historically MPL-2.0 | GitHub repo metadata | ambiguous NOT_VERIFIED | No code copied; only widget/UX patterns studied. |
| OpenMRS ESM dispensing / stock-management | MPL-2.0 heritage | repo metadata | low (study only) | No code copied. |
| Bahmni (frontend + docs) | Mixed; EPL/ECL heritage; new `bahmni-apps-frontend` lists "Other"/Apache — **NOT_VERIFIED** | GitHub repo metadata + Wiki | HIGHLY ambiguous | No code copied; docs CC BY-SA 4.0 also read only. |
| OpenEMR | **GPL-3.0** (copyleft, strong) | GitHub `rel-810`/`rel-800` `LICENSE`, open-emr.org | GPL-3.0 derivative work risk | No code copied. Used strictly as functional roadmap (`OPENEMR_GAP_ANALYSIS.md`). |
| fhir-ui (Health Intellect) | MIT | GitHub LICENSE (MIT, Copyright 2019 Brayton Stafford) | permissive; attribution suggested | No code copied; table/banner presentation concepts reimplemented in Jinja. |

## NOT_VERIFIED disclosures
- `openmrs/openmrs-esm-patient-chart` license field reads "Other" in the GitHub UI at
  audit time; we did not open a raw LICENSE file for it. Recorded as NOT_VERIFIED.
- `Bahmni/bahmni-apps-frontend` LICENSE field reads "Add license information here" in
  its docs; historical Bahmni projects are EPL/ECL. Recorded as NOT_VERIFIED.

## What was "borrowed" (non-code)
- Feature checklists and workflow patterns (public docs/wikis, e.g. OpenMRS chart
  README, OpenEMR Features wiki, Bahmni docs, fhir-ui README) — these are
  functional requirements, not copyrightable expression; reimplemented independently.
- Naming conventions inherited from the FHIR/clinical domain (PatientBanner,
  ObservationTable, OrderSet, ClinicalTemplate, Recall/Reminders) — standard domain
  terminology, not proprietary.

## Explicitly NOT reused
- React/TypeScript components (fhir-ui, Bahmni, OpenMRS ESM).
- PHP code (OpenEMR).
- Java/Kotlin (OpenMRS core).
- Any CSS/Sass from Carbon, Material-UI, or Bahmni/OpenMRS themes.
- Any database schema DDL from the above (iHIS schema is original and modeled on its
  own domain models).

## Library/license posture of iHIS
iHIS dependencies are standard Flask-ecosystem packages (MIT/BSD) — no GPL-3.0 or
MPL-2.0 runtime dependencies introduced by this exercise. Any future dependency must
pass an explicit license review before addition. PythonAnywhere deployment remains
unaffected.