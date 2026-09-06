# iHIS Role Capabilities

Authoritative summary of what each role can see and do. The source of truth is
`app/permissions.py` (`ROLE_PERMISSIONS`) plus the record-level need-to-know
policy in `app/access.py`. Route decorators (`roles_required`,
`permissions_required`, `patient_access_required`) enforce both layers; the
sidebar in `app/__init__.py` only shows links the role can actually open.

Demo accounts (all password `123456`, created by `python seed.py`, never in
production):

| Role | Account | Home page |
|------|---------|-----------|
| SuperAdmin | superadmin@ihis.com | `/super-admin/dashboard` |
| Admin | admin@ihis.com | `/admin/dashboard` |
| Doctor (displayed as **Physician**) | dr.ahmed@ihis.com | `/doctor/dashboard` |
| Nurse | nurse@ihis.com | `/nursing/dashboard` |
| LabTechnician | lab@ihis.com | `/lab/dashboard` |
| Radiologist | radio@ihis.com | `/radiology/dashboard` |
| RadiologyTechnician | radtech@ihis.com | `/radiology/dashboard` |
| Pharmacist | pharma@ihis.com | `/pharmacy/dashboard` |
| Physiotherapist | physio@ihis.com | `/physiotherapy/dashboard` |
| Dentist | dentist@ihis.com | `/dentistry/dashboard` |
| Receptionist | reception@ihis.com | `/reception/dashboard` |
| Cashier | cashier@ihis.com | `/billing/dashboard` |
| Patient | patient@ihis.com | `/patient/dashboard` |

## Two layers of access control

1. **Capability (RBAC).** A permission such as `LAB_RESULT_VERIFY` says the
   role may perform the action at all. Counts per role: SuperAdmin 85,
   Admin 57, Doctor 52, Nurse 32, Dentist 24, Pharmacist 23,
   Physiotherapist 16, Receptionist 15, Radiologist 14, LabTechnician 10,
   RadiologyTechnician 8, Cashier 6, Patient 0 (portal routes are gated by
   ownership instead).
2. **Need-to-know (record level).** Even with `PATIENT_VIEW`, a staff member
   only opens a patient they have a documented relationship with. Admin and
   SuperAdmin have hospital-wide read visibility, which is **not** a licence to
   edit finalized clinical records (locked statuses `Verified`, `Signed`,
   `Locked`, `Finalized` are enforced in `app/utils.py`).

### Need-to-know relationships by role

| Role | Patient is visible when… |
|------|--------------------------|
| Doctor | authored a record / diagnosis / order / prescription / referral / admission, has an appointment, is on the care team, or shares the patient's department |
| Nurse | patient is currently admitted, has an appointment today, nurse documented vitals/notes/MAR, or shares the department |
| LabTechnician | any lab order exists for the patient (or a result authored by the technician) |
| Radiologist / RadiologyTechnician | any radiology order exists (or a report authored by the radiologist) |
| Pharmacist | any prescription exists for the patient |
| Dentist | dental record / procedure / orthodontic plan, or a referral to a dental specialty |
| Physiotherapist | therapy assessment / plan / session, or a referral to physiotherapy/rehabilitation |
| Receptionist | created the appointment, admitted the patient, or the patient has an appointment today |
| Cashier | the patient has a bill |
| Patient | own record only |
| Admin / SuperAdmin | all patients (supervisory) |

## Capabilities per role

### SuperAdmin
- AI Control Center: requests today/this hour, cache hits/misses, failures, 429
  events, latency, feature and role usage, AI audit, unresolved critical alerts,
  budget switches, cache clearing.
- Command Center: hospital overview KPIs, Hospital Demo (10 guided
  scenarios), System Health, Platform Capabilities, audit log (paginated,
  filterable), backup creation/verification, roles and permissions, settings.
- Labelled **role preview** (banner + exit) and **patient-portal preview**
  (picker); never a silent impersonation.
- Everything Admin can do, plus lab/radiology/pharmacy write capabilities for
  supervision and recovery.

### Admin
- Staff, departments, doctors, statistics, service catalog, beds/wards.
- Reads all clinical data; can create/discharge admissions, record payments,
  void bills, manage alerts and reminders, run AI tools.
- Cannot sign clinical records, verify lab results, sign radiology reports or
  dispense.

### Doctor — shown everywhere as *Physician* (internal role id unchanged)
- Own worklist: appointments (start consultation → in consultation →
  complete), patients (need-to-know), Patient 360, medical records (draft,
  sign, amend with audit), diagnoses, problems, allergies, immunizations.
- Orders through the shared order service: lab, radiology, prescriptions
  (with safety screening), referrals (to physiotherapy, dentistry or another
  doctor), order sets, follow-ups, admissions and discharge.
- Cancels own prescriptions (MAR doses are discontinued, pharmacy task
  cancelled).
- Acknowledges results in the Smart Inbox (Critical / Results / Referrals /
  Pharmacy Interventions / Tasks / Messages) with a deterministic "what needs
  my attention first" ranking.
- **AI Clinical Copilot** (✦ in the header): smart patient summary, pre-visit
  summary, encounter/inpatient summaries, lab and radiology summaries and trends,
  AI-assisted differential support, HPI/SOAP/assessment/plan/follow-up/
  post-visit/discharge/referral drafts, medication/allergy/interaction/
  reconciliation review, patient-friendly summaries and education, Predictive AI
  (Dermatology, Radiology, Dentistry). Smart autocomplete and smart diagnosis
  entry on clinical forms; rule-based medication safety shown at prescribing;
  "Analyze with AI" on results. Every AI output is explicit, labelled and
  reviewed; nothing writes to the chart.

### Nurse
- Scoped dashboard (admitted patients, due/overdue doses, abnormal vitals).
- Vitals, nursing notes, care plans, risk assessments, intake/output,
  medication administration (MAR) with outcome guards.
- Cannot alter the source prescription.

### LabTechnician
- Worklist by state machine: Pending → Accepted → Collected → ReceivedAtLab →
  Processing → Resulted → Verified → Finalized, plus Rejected / Reordered /
  Cancelled. Cancel is offered for never-accepted or accepted orders.
- Enters, verifies (completes the task and notifies the ordering doctor) and
  amends results; critical values escalate to the ordering clinicians.

### Radiologist
- Orders worklist, reporting, amendment (versioned), signing (notifies
  ordering clinicians, closes tasks), critical-findings register with manual
  flagging, implant verification, radiation-dose capture, dose dashboard,
  safety screening, AI image analysis.

### RadiologyTechnician
- Schedule → arrive → perform → upload images → record dose. Performing a
  study creates the reporting task for the Radiologist. Cannot report or sign.

### Pharmacist
- Prescription queue, dispensing (FEFO, expired batches excluded, atomic
  stock), inventory and stock adjustments (never negative), reconciliation,
  drug-interaction checks, clinical interventions raised from the prescription
  page (the prescriber is notified directly), AI clinical-pharmacist workbench.

### Dentist
- Dental dashboard, odontogram (tooth surfaces, current state), dental records,
  procedures (bill on completion), orthodontic plans, referrals in.

### Physiotherapist
- Rehabilitation dashboard, assessments, plans, sessions (state machine),
  progress notes, exercise library, referrals in.

### Receptionist
- Front desk: register patients (MRN assigned), book / reschedule / cancel /
  check-in (queue number), waiting queue (today), admissions intake, invoices.

### Cashier
- Cashier desk, invoices, payments and receipts (idempotent), revenue report.
  No access to clinical documentation.

### Patient
- Own portal around MY HEALTH / MY APPOINTMENTS / MY MEDICATIONS / MY RESULTS /
  MY DOCUMENTS / MY FOLLOW-UP / MY MESSAGES / MY NOTIFICATIONS, plus My Health
  Summary (verified data only; clinician-only content hidden).
- Safe patient AI: explain a released result, explain a medicine, prepare for an
  appointment, explain a term, summarise the record, prepare questions. Own
  record only; never diagnoses or changes treatment.
