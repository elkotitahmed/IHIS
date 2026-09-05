# OPENEMR GAP ANALYSIS — iHIS vs OpenEMR (GPL-3.0)

> **License note (important):** OpenEMR is released under **GNU GPL v3** (verified on
> github.com/openemr/openemr, `rel-810`/`rel-800` branches and open-emr.org).
> Copyleft means iHIS must **not** copy any OpenEMR source code. This analysis uses
> OpenEMR purely as a **functional roadmap** (feature checklist), which is license-neutral.
> Every gap below is reimplemented natively in Flask/Jinja/SQLAlchemy.

Verified via OpenEMR wiki "Features", open-emr.org, and `API_README.md` (2026-09-05).

## OpenEMR capability areas    → iHIS equivalence

| OpenEMR area | iHIS | Assessment |
|--------------|------|-----------|
| Encounters & SOAP notes | `MedicalRecord` (EMR add), SOAP AI, care flows | ✅ partial — no reusable note templates (gap, P1) |
| Medical Issues (problems) | `Problem` model + Patient 360 | ✅ done |
| Medications / e-Rx | `Prescription` + items + interactions + pharmacy/no-edit rules | ✅ done (no external e-Rx, out of scope) |
| Immunizations | `ImmunizationRecord` | ⚠️ partial — no recall/due-engine (gap, P1) |
| Vitals & growth | `VitalSign` + care-strip; growth monitoring | ✅ done |
| Template-driven forms & clinical notes | native forms (EMR/physical/dental/physio) | ⚠️ partial — no user-defined templates (gap, P1) |
| Patient flow board / tracks / check-in | reception queue + admission flow | ✅ done |
| Recall (reminders) board | follow-ups scheduled; no due-engine | ⚠️ partial — reminder engine (gap, P1) |
| Scheduling (multi-facility, repeat, restriction) | appointments + queue | ✅ done (single-facility by design) |
| Patient portal (messaging, payments, docs, results) | portal module | ✅ done |
| Results (labs) | `LabOrder`/`LabResult`, worklist | ⚠️ partial — acknowledgement log + worklist polish (gap, P1/P2) |
| Patient summary (CCDA-like) | Patient 360 + clinical summary (new) | ⚠️ partial — verification (gap, P1) |
| Billing (CPT/HCPCS/ICD, 5010 claims) | billing module + payments idempotency; ICD-10 coding | ✅ partial — claims exchange out of scope |
| Insurance eligibility / AR / EOB | not present | out of scope (notes in README roadmap) |
| FHIR API | JSON API endpoints; serializers | ⚠️ deferred (Phase 31 foundation) |

## OpenEMR specific items → verdict

1. **ONC-certified FHIR API** — not required; iHIS targets its own JSON API. ❌ not planned.
2. **Template-driven forms** → **`ClinicianTemplate`** (P1, this cycle). ✅
3. **Recall/reminder board** → **`ClinicalReminder` engine** (P1, this cycle). ✅
4. **ANSI X12 / ERA claim submission** — external clearinghouse integration; out of scope. ❌
5. **Insurance eligibility queries** — out of scope (regional). ❌
6. **Encounter flag / check-in workflow** — already covered by queue/admission. ✅

## Scorecard
- OpenEMR capabilities fully matched natively in iHIS: **~20/27** (completing P1 gaps → ~25).
- Remaining intentionally out of scope: FHIR server, X12/ERA, insurance eligibility,
  HIE/Direct messaging, device connectivity.

See `FEATURE_GAP_MATRIX.md` for prioritization and `OPEN_SOURCE_VALUE_SCORE.md` for
cost/benefit of adding the remaining items.