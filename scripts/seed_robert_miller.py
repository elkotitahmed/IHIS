"""Create the clinical-pharmacy demo patient **Robert Miller** (idempotent).

    python scripts/seed_robert_miller.py

68-year-old man, 82 kg, admitted with an acute gout flare on a background of
non-valvular atrial fibrillation (CHA2DS2-VASc 4), CKD stage 3b, hypertension
and dyslipidaemia. NKDA. Home medications: apixaban 5 mg BID, diltiazem ER
240 mg daily, lisinopril 20 mg daily, atorvastatin 40 mg nightly. A new
colchicine order (1.2 mg then 0.6 mg after 1 h) is waiting for pharmacist
verification.

Everything is written through the same services the UI uses, so the
prescription-safety engine, the pharmacy queue, tasks, notifications, the
timeline and the clinical-pharmacist review card all see one consistent chart:

* users/patient/problem list/encounter diagnoses/admission note
* admission to a ward bed (bed claimed), nursing vitals (weight for CrCl)
* verified laboratory results: creatinine (with a 3-month-old baseline), eGFR,
  potassium, AST, ALT, bilirubin, uric acid
* prescription #1 — home medications continued on admission (4 lines)
* prescription #2 — colchicine gout-flare course (the one to review)
  -> clinical alerts raised by the rules engine, pharmacy task, notification
* admission medication reconciliation (home list vs active orders)
* the documented pharmacist intervention (hold + alternatives) sent to the
  prescriber, so the closed loop can be shown end-to-end.

Login: robert.miller@ihis.com / 123456 (demo password, development only).
"""
import json
import os
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('FLASK_CONFIG', 'development')

from app import create_app, db  # noqa: E402

EMAIL = 'robert.miller@ihis.com'
DOB = date(1958, 3, 14)

HOME_MEDS = [
    # generic, brand, dosage, frequency, instructions, qty
    ('Apixaban', 'Eliquis', '5 mg', 'Twice daily', 'Non-valvular AF (CHA2DS2-VASc 4). Home medication continued.', 60),
    ('Diltiazem', 'Cartia XT', '240 mg ER', 'Once daily', 'Rate control. Home medication continued.', 30),
    ('Lisinopril', 'Zestril', '20 mg', 'Once daily', 'Hypertension. Home medication continued.', 30),
    ('Atorvastatin', 'Lipitor', '40 mg', 'Once nightly', 'Dyslipidaemia. Home medication continued.', 30),
]

LABS = [
    # test name, value, unit, days ago, notes
    ('Serum Creatinine', '1.8', 'mg/dL', 92, 'Baseline (outpatient nephrology follow-up).'),
    ('Serum Creatinine', '2.1', 'mg/dL', 0, 'Above baseline of 1.8 mg/dL.'),
    ('eGFR (CKD-EPI)', '32', 'mL/min/1.73m2', 0, 'CKD stage 3b.'),
    ('Serum Potassium', '5.2', 'mEq/L', 0, 'Mild hyperkalaemia on ACE inhibitor.'),
    ('AST (SGOT)', '28', 'U/L', 0, ''),
    ('ALT (SGPT)', '32', 'U/L', 0, ''),
    ('Total Bilirubin', '0.9', 'mg/dL', 0, ''),
    ('Uric Acid', '9.4', 'mg/dL', 0, 'Hyperuricaemia; acute gout flare.'),
]

PROBLEMS = [
    ('M10.9', 'Acute gouty arthritis exacerbation (right first MTP)', 'Severe', 0,
     'Current chief complaint. Uric acid 9.4 mg/dL.'),
    ('I48.91', 'Non-valvular atrial fibrillation', 'Moderate', 1460,
     'CHA2DS2-VASc = 4. Anticoagulated with apixaban; rate control with diltiazem.'),
    ('N18.32', 'Chronic kidney disease, stage 3b', 'Moderate', 1100,
     'Baseline SCr 1.8 mg/dL; eGFR 32 mL/min.'),
    ('I10', 'Essential hypertension', 'Moderate', 3650, ''),
    ('E78.5', 'Dyslipidaemia', 'Mild', 2900, ''),
]


def _user(email):
    from app.models import User
    return User.query.filter_by(email=email).first()


def main():
    app = create_app(os.environ.get('FLASK_CONFIG', 'development'))
    with app.app_context():
        if not app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite:'):
            raise SystemExit('Demo patient seeding is only allowed on a local SQLite database.')

        # Catalogue (roles, wards, lab tests, medications, interaction pairs) — idempotent.
        import seed as seed_mod
        seed_mod.seed_roles_and_permissions(app)

        from flask_login import login_user
        from app.models import (Admission, Bed, Department, Diagnosis, Doctor, LabOrder, LabResult,
                                LabTestCatalog, MedicalRecord, Medication, Patient, PharmacyIntervention,
                                Problem, Role, User, VitalSign, Ward)
        from app.services import clinical_orders, reconciliation as recon_svc
        from app.services.laboratory import evaluate_abnormality
        from app.services.notifications import notify
        from app.services.timeline import record_event
        from app.utils import assign_mrn, utcnow

        doctor_user = _user('dr.ahmed@ihis.com') or User.query.filter_by(user_type='doctor').first()
        doctor = Doctor.query.filter_by(user_id=doctor_user.id).first() if doctor_user else None
        pharmacist = _user('pharma@ihis.com') or User.query.filter_by(user_type='pharmacist').first()
        nurse = _user('nurse@ihis.com') or User.query.filter_by(user_type='nurse').first()
        lab_tech = _user('lab@ihis.com')
        if not (doctor and pharmacist and nurse):
            raise SystemExit('Run python seed.py first (staff accounts missing).')

        now = utcnow()
        admitted_at = now - timedelta(hours=6)

        # ---- patient account + chart --------------------------------------
        user = _user(EMAIL)
        if user is None:
            user = User(username='robert.miller', email=EMAIL, full_name='Robert Miller', user_type='patient',
                        phone='+1 555 0142')
            user.set_password('123456')
            user.roles.append(Role.query.filter_by(name='Patient').first())
            db.session.add(user)
            db.session.flush()
        patient = Patient.query.filter_by(user_id=user.id).first()
        created = patient is None
        if created:
            im = Department.query.filter_by(name='Internal Medicine').first()
            patient = Patient(user_id=user.id, date_of_birth=DOB, gender='Male', phone='+1 555 0142',
                              address='42 Maple Street, Springfield', blood_type='A+',
                              allergies='NKDA (No Known Drug Allergies)',
                              chronic_diseases='Non-valvular atrial fibrillation (CHA2DS2-VASc 4); CKD stage 3b; '
                                               'hypertension; dyslipidaemia; gout',
                              emergency_contact='Margaret Miller (wife) +1 555 0143',
                              department_id=im.id if im else None)
            db.session.add(patient)
            db.session.flush()
            assign_mrn(patient)
        else:
            print(f'Patient already exists (#{patient.id} {patient.mrn}); adding only what is missing.')
        db.session.commit()

        with app.test_request_context():
            login_user(doctor_user)

            # Problem list + encounter diagnoses
            if not Problem.query.filter_by(patient_id=patient.id).first():
                for code, desc, sev, days, notes in PROBLEMS:
                    db.session.add(Problem(patient_id=patient.id, icd10_code=code, description=desc, severity=sev,
                                           onset=(now - timedelta(days=days)).date(), status='Active', notes=notes,
                                           recorded_by=doctor_user.id))
                for i, (code, desc, sev, days, notes) in enumerate(PROBLEMS):
                    db.session.add(Diagnosis(patient_id=patient.id, doctor_id=doctor.id, icd10_code=code,
                                             description=desc, is_primary=(i == 0), notes=notes,
                                             date_diagnosed=admitted_at if i == 0 else now - timedelta(days=days)))

            # Admission note (encounter)
            if not MedicalRecord.query.filter_by(patient_id=patient.id).first():
                mr = MedicalRecord(
                    patient_id=patient.id, doctor_id=doctor.id, visit_date=admitted_at,
                    diagnosis='Acute gouty arthritis exacerbation, right first MTP joint',
                    treatment_plan='Admit for pain control and renal monitoring. Anti-inflammatory therapy for the '
                                   'flare (order sent to pharmacy for verification). Continue apixaban, diltiazem, '
                                   'lisinopril, atorvastatin. Avoid NSAIDs (CKD 3b, anticoagulated). Repeat '
                                   'creatinine and potassium in 24 h.',
                    clinical_notes='68 M, 82 kg. 2 days of severe pain, swelling and erythema of the right first '
                                   'MTP joint; afebrile, no trauma. Known non-valvular AF on apixaban, CKD 3b '
                                   '(baseline SCr 1.8), hypertension, dyslipidaemia. NKDA. Exam: exquisitely '
                                   'tender, warm, swollen right 1st MTP; irregularly irregular pulse 88/min; '
                                   'BP 146/88. Labs: SCr 2.1 (above baseline), eGFR 32, K 5.2, uric acid 9.4, '
                                   'LFTs normal.',
                    status='Signed', signed_by=doctor_user.id, signed_at=admitted_at)
                db.session.add(mr)
                record_event(patient.id, 'ENCOUNTER', 'Admission note signed',
                             'Acute gout flare · AF on apixaban · CKD 3b', source_type='medical_record',
                             source_id=None, department='Doctor', occurred_at=admitted_at)

            # Admission to a ward bed
            adm = Admission.query.filter_by(patient_id=patient.id, status='Admitted').first()
            if adm is None:
                ward = Ward.query.filter_by(name='General Ward A').first() or Ward.query.first()
                bed = Bed.query.filter_by(ward_id=ward.id, status='Available').order_by(Bed.bed_no).first()
                if bed is None:
                    bed = Bed.query.filter_by(ward_id=ward.id).first()
                bed.status = 'Occupied'
                adm = Admission(patient_id=patient.id, ward_id=ward.id, bed_id=bed.id, admitting_doctor_id=doctor.id,
                                admitted_by=doctor_user.id, admitted_at=admitted_at,
                                expected_discharge=now + timedelta(days=2),
                                reason='Acute gouty arthritis exacerbation; AF on apixaban; CKD 3b — pain control '
                                       'and renal monitoring', status='Admitted')
                db.session.add(adm)
                db.session.flush()
                adm.admission_no = f'ADM-{1000 + adm.id}'
                record_event(patient.id, 'ADMISSION', f'Admitted to {ward.name} — Bed {bed.bed_no}',
                             f'{adm.admission_no} · {adm.reason}', source_type='admission', source_id=adm.id,
                             department='Admissions', occurred_at=admitted_at)

            # Nursing vitals (weight feeds the creatinine-clearance estimate)
            if not VitalSign.query.filter_by(patient_id=patient.id).first():
                db.session.add(VitalSign(patient_id=patient.id, nurse_id=nurse.id, temperature=37.8,
                                         blood_pressure_systolic=146, blood_pressure_diastolic=88, heart_rate=88,
                                         respiratory_rate=18, oxygen_saturation=96, height_cm=178.0, weight_kg=82.0,
                                         pain_score=8, recorded_at=admitted_at + timedelta(minutes=20)))
                record_event(patient.id, 'VITALS', 'Admission vitals recorded',
                             'T 37.8 · BP 146/88 · HR 88 (irregular) · SpO2 96% · 82 kg · pain 8/10',
                             department='Nursing', occurred_at=admitted_at + timedelta(minutes=20))

            # Laboratory results (verified)
            if not LabOrder.query.filter_by(patient_id=patient.id).first():
                for name, value, unit, days_ago, notes in LABS:
                    test = LabTestCatalog.query.filter_by(test_name=name).first()
                    if test is None:
                        print(f'  ! lab test missing in catalogue: {name}')
                        continue
                    when = (admitted_at + timedelta(minutes=45)) if days_ago == 0 else now - timedelta(days=days_ago)
                    order = LabOrder(patient_id=patient.id, doctor_id=doctor.id, test_id=test.id, status='Verified',
                                     priority='Urgent' if days_ago == 0 else 'Normal', order_date=when,
                                     specimen_type='Blood', specimen_status='Received',
                                     collected_by=nurse.id, collection_time=when + timedelta(minutes=10),
                                     received_at_lab=when + timedelta(minutes=30), notes=notes or None)
                    db.session.add(order)
                    db.session.flush()
                    order.accession_number = f'ACC-{order.id:06d}'
                    v = float(value)
                    crit = (test.critical_low is not None and v < test.critical_low) or \
                           (test.critical_high is not None and v > test.critical_high)
                    db.session.add(LabResult(order_id=order.id, result_value=value, result_unit=unit,
                                             result_notes=notes or None, status='Verified',
                                             is_abnormal=evaluate_abnormality(value, test.normal_range) or bool(crit),
                                             is_critical=bool(crit),
                                             validated_by=(lab_tech.id if lab_tech else None),
                                             created_by=(lab_tech.id if lab_tech else None),
                                             result_date=when + timedelta(hours=1)))
                record_event(patient.id, 'LAB', 'Admission bloods verified',
                             'SCr 2.1 mg/dL (baseline 1.8) · eGFR 32 · K 5.2 · uric acid 9.4 · LFTs normal',
                             department='Laboratory', occurred_at=admitted_at + timedelta(hours=2))
            db.session.commit()

            # Prescription #1: home medications continued on admission
            meds = {m.generic_name: m for m in Medication.query.all()}
            missing = [g for g, *_ in HOME_MEDS if g not in meds] + (['Colchicine'] if 'Colchicine' not in meds else [])
            if missing:
                raise SystemExit(f'Medications missing from the formulary: {missing}')
            from app.models import Prescription, PrescriptionItem
            has_home = (PrescriptionItem.query.join(Prescription).filter(
                Prescription.patient_id == patient.id, PrescriptionItem.medication_id == meds['Apixaban'].id).first())
            if not has_home:
                rx_home = clinical_orders.create_prescription(
                    patient, doctor,
                    [{'medication_id': meds[g].id, 'dosage': dose, 'frequency': freq, 'duration': 'Ongoing',
                      'instructions': instr, 'quantity': qty} for g, _b, dose, freq, instr, qty in HOME_MEDS],
                    refills=0, origin='Admission: home medications continued')
                rx_home.prescribed_date = admitted_at + timedelta(hours=1)
                db.session.commit()
                print(f'  home-medication prescription #{rx_home.id} created')

            # Prescription #2: colchicine flare course — the one under pharmacist review
            has_colch = (PrescriptionItem.query.join(Prescription).filter(
                Prescription.patient_id == patient.id, PrescriptionItem.medication_id == meds['Colchicine'].id).first())
            if not has_colch:
                rx_new = clinical_orders.create_prescription(
                    patient, doctor,
                    [{'medication_id': meds['Colchicine'].id, 'dosage': '1.2 mg, then 0.6 mg after 1 hour',
                      'frequency': 'Single course (stat)', 'duration': '1 day', 'quantity': 3,
                      'instructions': 'Acute gout flare, right first MTP. Give 1.2 mg (2 x 0.6 mg tablets) '
                                      'immediately, then 0.6 mg one hour later.'}],
                    refills=0, origin='Gout flare')
                rx_new.prescribed_date = now - timedelta(hours=2)
                db.session.commit()
                print(f'  colchicine prescription #{rx_new.id} created (alerts raised by the safety engine)')
            else:
                rx_new = has_colch.prescription

            # Admission medication reconciliation (home list vs active orders)
            from app.models import MedicationReconciliation
            if not MedicationReconciliation.query.filter_by(patient_id=patient.id).first():
                home_list = [{'name': g, 'dose': dose, 'frequency': freq} for g, _b, dose, freq, _i, _q in HOME_MEDS]
                rec, discs = recon_svc.run_reconciliation(
                    patient.id, pharmacist_id=pharmacist.id, home_medications=json.dumps(home_list),
                    admission_id=adm.id, reconciliation_type='Admission',
                    summary='Admission reconciliation: four home medications confirmed with the patient and his '
                            'wife; all continued. New inpatient order: colchicine (under review).')
                record_event(patient.id, 'MEDICATION', 'Admission medication reconciliation started',
                             f'{len(home_list)} home medications · {len(discs)} discrepancy finding(s)',
                             source_type='reconciliation', source_id=rec.id, department='Pharmacy')
                db.session.commit()
                print(f'  reconciliation #{rec.id} created ({len(discs)} discrepancies)')

            # Documented pharmacist intervention on the colchicine order (closed loop with the prescriber)
            if not PharmacyIntervention.query.filter_by(patient_id=patient.id).first():
                login_user(pharmacist)
                issue = ('Colchicine 1.2 mg + 0.6 mg with diltiazem (P-gp / CYP3A4 inhibitor) in CKD stage 3b '
                         '(SCr 2.1, eGFR 32, CrCl ~35-39 mL/min): reduced colchicine clearance on two fronts — '
                         'risk of life-threatening toxicity (rhabdomyolysis, bone-marrow suppression, multi-organ '
                         'failure). Standard loading dose is inappropriate. Also: apixaban + diltiazem (bleeding, '
                         'monitor; 1 of 3 dose-reduction criteria), lisinopril with K+ 5.2, atorvastatin + '
                         'diltiazem (myopathy).')
                reco = ('HOLD colchicine and contact prescriber. Option A (preferred): prednisone 30 mg PO daily '
                        'for 5 days then stop (or taper over 7-10 days) — safe in renal impairment, no CYP3A4/P-gp '
                        'interaction. Option B (if steroids contraindicated): colchicine 0.3 mg PO single dose '
                        '(max 0.6 mg), not repeated for at least 14 days. Avoid NSAIDs (AKI risk, apixaban '
                        'bleeding). Monitor K+ closely on lisinopril; consider potassium binder if > 5.0. Keep '
                        'atorvastatin <= 40 mg or switch to rosuvastatin / pravastatin.')
                iv = PharmacyIntervention(patient_id=patient.id, prescription_id=rx_new.id, pharmacist_id=pharmacist.id,
                                          prescriber_id=doctor_user.id, issue=issue, severity='Contraindicated',
                                          recommendation=reco, category='CONTRAINDICATION', status='COMMUNICATED')
                db.session.add(iv)
                db.session.flush()
                record_event(patient.id, 'MESSAGE', f'Pharmacist intervention on Rx #{rx_new.id}',
                             f'Contraindicated · {issue[:120]}', source_type='intervention', source_id=iv.id,
                             department='Pharmacy')
                notify(doctor_user.id, f'Pharmacy intervention on Rx #{rx_new.id}',
                       'Colchicine held: diltiazem interaction + CKD 3b. Prednisone 30 mg x 5 d suggested — please '
                       'review the recommendation.', notification_type='critical',
                       entity_type='pharmacy_intervention', entity_id=iv.id)
                db.session.commit()
                print(f'  pharmacist intervention #{iv.id} recorded and sent to the prescriber')

        # ---- summary --------------------------------------------------------
        from app.models import ClinicalAlert
        from app.services.medication_review import review_prescription
        alerts = ClinicalAlert.query.filter_by(patient_id=patient.id, status='OPEN').all()
        rv = review_prescription(rx_new)
        print(f'\nRobert Miller: patient #{patient.id} MRN {patient.mrn} · login {EMAIL} / 123456')
        print(f'  open alerts: {len(alerts)} -> ' + ', '.join(f"{a.severity} {a.alert_type}" for a in alerts))
        r = rv['renal']
        print(f"  renal: SCr {r['scr']} {r['scr_unit']} · CrCl {r['crcl']} mL/min ({r['method']}) · eGFR {r['egfr']} · K {r['potassium']}")
        print(f"  colchicine Rx #{rx_new.id} verdict: {rv['level']} ({len(rv['findings'])} findings)")
        for f in rv['findings']:
            print(f"    [{f['severity']}] {f['title']}")


if __name__ == '__main__':
    main()
