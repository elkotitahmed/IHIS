# iHIS Demo Guide

A scripted walk-through for showing the platform end to end. Every step uses
real records created by `python seed.py` (optionally `python seed_patients.py`
for more volume). Never run the demo seed in production.

## Before the demo

```bash
python seed.py
python run.py
```

Open http://127.0.0.1:5000 and sign in as **superadmin@ihis.com / 123456**.
Every demo account uses the password `123456` (see `docs/ROLE_CAPABILITIES.md`).

Optional: switch the language toggle (top bar) to Arabic to show RTL, and the
moon icon for dark mode.

## 1. Command Center (SuperAdmin)

- **Hospital Overview** `/super-admin/dashboard`: live KPIs (patients, visits
  today, pending lab/radiology, admitted census, low stock, open tasks).
- **Hospital Demo** `/super-admin/demo`: ten scenarios, each step linked to
  the real record it touches. Use it as the agenda:
  1. Outpatient visit
  2. Doctor → Laboratory
  3. Doctor → Radiology
  4. Doctor → Pharmacy
  5. Nursing medication (MAR)
  6. Physiotherapy
  7. Dentistry
  8. Referral
  9. Billing and payment
  10. Patient portal
- **System Health** `/super-admin/system-health`: database, migration head,
  storage, secrets, CSRF, rate limiting, AI providers, image models, latest
  backup, FHIR entry point, and the preventive-care sweep button.
- **Preview as role** (banner select): opens the workspace of any role with a
  clearly labelled preview banner and an exit link.

## 2. The clinical loop (15 minutes)

1. **Reception** (reception@ihis.com): register a patient (MRN is assigned),
   book an appointment with Dr. Ahmed for today, check the patient in (queue
   number appears in the waiting queue).
2. **Doctor** (dr.ahmed@ihis.com): the appointment is on the dashboard. Click
   *Start* to open the patient, add a consultation note, order a CBC, a chest
   X-ray and a prescription. Each order creates a department task and a
   timeline event. Open **Patient 360** to show the merged timeline.
3. **Laboratory** (lab@ihis.com): accept → collect → receive → process →
   enter result → verify. Verification completes the task and notifies the
   ordering doctor. Enter a critical value to show the escalation.
4. **Radiology technician** (radtech@ihis.com): schedule → arrive → perform →
   upload an image. Performing creates the reporting task.
5. **Radiologist** (radio@ihis.com): write the report, flag a critical finding,
   sign. The ordering clinician is notified.
6. **Pharmacy** (pharma@ihis.com): review the prescription, run the
   interaction check, raise an intervention (prescriber notified) or dispense
   (stock decremented atomically; expired batches never used).
7. **Nurse** (nurse@ihis.com): the MAR shows the due doses; administer one,
   record vitals with a pain score.
8. **Doctor**: open the **Clinical Inbox**; acknowledge the lab result and the
   radiology report; complete the appointment.
9. **Cashier** (cashier@ihis.com): the invoice was generated from completed
   services; record a payment and print the receipt.
10. **Patient** (patient@ihis.com): the portal shows the visit, results,
    prescription and balance.

## 3. Specialty and inpatient extras

- **Admissions**: admit the patient to a ward bed (atomic bed claim), show the
  admission page, discharge with a diagnosis and an automatic follow-up.
- **Referral to physiotherapy**: create from the doctor's Referrals page; the
  physiotherapist accepts it (task closes, referrer notified), creates a plan
  and a session.
- **Dentistry**: odontogram with tooth surfaces, a procedure billed on
  completion.

## 4. AI Clinical Copilot (the demo's centrepiece)

1. **Physician** (dr.ahmed@ihis.com): the dashboard shows six priority tiles
   and only TODAY open. Click **✦ AI Copilot** in the header → the panel opens
   in patient context on any patient page (or choose a recent patient).
   Run *Smart Patient Summary*: verified chart data appears first, then the
   labelled AI-assisted section with thumbs-up/down.
2. Open **Radiology AI** from the Predictive AI group → pick a report →
   the AI Clinical Assistance column shows potential findings, severity, why
   flagged and the recommended action, separately from the radiologist's
   report. Enter a report mentioning "tension pneumothorax" as the
   radiologist (radio@ihis.com) to trigger the full workflow: red banner for
   the physician, urgent task, notification, acknowledge → action → resolve.
3. On a prescription form, choose Warfarin then Aspirin: the rule-based
   interaction warning appears immediately; *AI review* is a separate button.
4. In the Smart Inbox click **What needs my attention first?** — the
   deterministic ranking is shown; the AI only narrates it.
5. **Dermatology AI**: upload a lesion photo → image quality score,
   AI-ASSISTED / NOT A FINAL DIAGNOSIS labels, differential considerations,
   ABCDE indicators and the physician review form.
6. **Dentistry AI**: chart summary, finding summary, treatment-plan draft,
   education, abnormality assistance for a patient with a dental chart.
7. **Patient** (patient@ihis.com): MY HEALTH SUMMARY, then *Explain with AI*
   on a released result or medicine — plain language, always ending with
   when to contact the physician.
8. **SuperAdmin**: AI Control Center shows the budget, usage and audit of
   everything you just did (no prompts stored).

## 4b. Other AI tools

Gemini-backed tools fall back to rule-based output when `GEMINI_API_KEY` is
absent, and never leak provider errors or keys to the page:

- Clinical summary, diagnosis support, medication review, SOAP draft, ICD-10
  coding, patient-friendly explanation and smart order suggestions from the
  doctor's patient page.
- Image analysis: fracture detection (YOLOv8), tooth segmentation (U-Net),
  skin lesion classification (ResNet-50 + EfficientNet-B0) at
  `/ai/fracture-detection`, `/ai/tooth-segmentation`,
  `/ai/skin-lesion-detection`. Images are stored privately and served only
  through the login-gated `/ai/media` route. The models require the ML
  dependencies from `requirements.txt` (the venv), not a system interpreter.

## 5. Governance

- **Audit log** `/super-admin/audit-logs`: filter by action, paginated.
- **Backup** `/super-admin/backup`: create and verify a backup; restore is a
  documented manual step (`docs/BACKUP_AND_RECOVERY.md`).
- **FHIR** `/fhir/patients`: read-only R4 Patient bundle for integration
  demos.
