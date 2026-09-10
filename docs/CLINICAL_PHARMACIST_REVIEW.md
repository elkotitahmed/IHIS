# Clinical pharmacist review (rules engine)

`app/services/medication_review.py` — deterministic, reproducible, no language
model. It is the **authoritative** layer under every medication-safety surface
in iHIS; Gemini (when configured) only elaborates on its findings and is told
not to contradict them.

## What it reads from the chart

| Input | Source |
|-------|--------|
| Serum creatinine, eGFR, potassium (latest numeric result) | `lab_orders` + `lab_results` + `lab_test_catalog` (test name match: *creatinine*, *egfr/gfr*, *potassium*) |
| Weight | latest `vital_signs.weight_kg` (nursing) |
| Age, sex | `patients.date_of_birth`, `patients.gender` |
| Active medications | every `prescription_items` line on an *Active* or *Dispensed* prescription (not only the lines on the prescription being reviewed) |
| Allergies | structured `allergies` rows + the free-text field (NKDA is recognised) |
| Formulary interaction pairs | `drug_interactions` (severity, mechanism, management) |
| Reference interactions | `app/data/ddinter.tsv` (DDInter, CC BY-NC-SA 4.0) |

Creatinine clearance is estimated with **Cockcroft-Gault** (the estimate drug
labels are written for); µmol/L creatinine is converted. When weight or age is
missing the reported eGFR is used instead and the card says which.

## Rules

* **Renal dose rules** (`RENAL_RULES`): ~35 common drugs with the CrCl
  threshold, severity and the label's advice (colchicine, apixaban criteria,
  rivaroxaban, dabigatran, enoxaparin, metformin, sulfonylureas, sitagliptin,
  ACE inhibitors, spironolactone, allopurinol, gabapentinoids, digoxin,
  antibiotics, opioids, lithium, baclofen, potassium, all NSAIDs …).
* **Inhibitor + impaired clearance**: colchicine with a P-gp / CYP3A4 inhibitor
  (diltiazem, verapamil, macrolides, azoles, ritonavir, ciclosporin, amiodarone…)
  when CrCl < 50 mL/min → *Contraindicated* with the alternative therapy spelled
  out. Evaluated from either side of the pair.
* **Apixaban dose reduction**: counts age ≥ 80, weight ≤ 60 kg, SCr ≥ 1.5 mg/dL;
  ≥ 2 → reduce to 2.5 mg BID (Major), 1 → informational.
* **Drug-lab**: potassium-raising drugs (ACEi/ARB/MRA, K supplements,
  trimethoprim …) with K > 5.0 (Moderate) / > 5.5 (Major).
* **Interactions**: local formulary pair first (mechanism + management), else
  DDInter level. One finding per pair; a label contraindication supersedes the
  plain pair.
* **Allergy conflicts** (exact / class cross-reactivity) and **duplicates**.

Severity ladder: Contraindicated > Major > Moderate > Minor > Info →
prescription verdict CRITICAL / HIGH / MODERATE / LOW / OK.

## Where it is used

| Surface | Behaviour |
|---------|-----------|
| `flag_prescription_safety` (every new prescription, incl. order sets) | one OPEN `ClinicalAlert` per finding (`DRUG_INTERACTION`, `DRUG_DISEASE`, `ALLERGY`, `DUPLICATE_THERAPY`) with the management text in the message; idempotent, no duplicate for the same pair across prescriptions |
| `/pharmacy/prescriptions/<id>` | *Clinical pharmacist review* card: verdict, renal panel (CrCl, SCr, eGFR, K, age/weight), findings with management and source, "Intervene" buttons that pre-fill the intervention form (category, severity, issue, recommendation) |
| `/pharmacy/ai-workbench` | *Rules verdict* column per patient, worst first |
| `/ai/medication-review/<patient>` | the same card above the optional Gemini review; the Gemini prompt receives the findings as an authoritative block |
| Patient header safety strip | the alerts raised above |

## Demo patient

`python scripts/seed_robert_miller.py` creates Robert Miller (68 M, 82 kg,
AF on apixaban + diltiazem, CKD 3b with SCr 2.1 / eGFR 32, K 5.2 on lisinopril,
atorvastatin 40 mg) with a colchicine 1.2 mg + 0.6 mg gout-flare order waiting
in the pharmacy queue. The rules return **CRITICAL**: colchicine + diltiazem
with CrCl ≈ 39 mL/min (contraindicated; prednisone suggested), colchicine renal
dose rule, colchicine + atorvastatin myopathy; on the home list: apixaban +
diltiazem (monitor, 1 of 3 dose criteria), atorvastatin + diltiazem, lisinopril
with K 5.2. A documented pharmacist intervention (hold; prednisone 30 mg × 5 d
or colchicine 0.3 mg single dose) is already sent to the prescriber.

`python scripts/purge_patients.py` removes every patient (backup first).

## Tests

`tests/test_medication_review.py` — renal function (Cockcroft-Gault, eGFR
fallback, µmol conversion), the colchicine contraindication, cross-prescription
interactions, apixaban criteria, potassium rule, metformin, clean prescription,
patient-wide de-duplication, alert generation/idempotency, pharmacist page,
workbench verdict and the AI review page without calling Gemini.
