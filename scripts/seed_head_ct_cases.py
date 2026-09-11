"""Three emergency head-CT cases for **Head CT Haemorrhage Detection** (idempotent).

    python scripts/seed_head_ct_cases.py

Each case is a real chart, linked the way the workflow expects, so the model
can be run on it directly from the page (``/ai/ich-detection?doc=<id>``), from
the patient's documents, or from the radiology worklist:

* an Emergency Medicine physician (created once: dr.omar@ihis.com) who sees the
  patient in the ED, documents the assessment and orders the urgent head CT;
* patient account + chart under the Emergency department, problem list with
  ICD-10 codes, ED vital signs, signed ED assessment note;
* an ED visit (walk-in appointment) with the physician — the documented
  relationship that need-to-know access requires;
* a **CT Scan order (Urgent)** created through the real ordering service
  (radiology task + notification), advanced to *Performed* with the acquired
  image attached to the order;
* the same image attached to the chart as a clinical document, so the AI page
  can open it with one click.

The AI is **not** run here: that is the live demo step for the radiologist.
Images were supplied by the project team as JPEG (not DICOM); the page states
that flat images lose the subdural and bone windows.

Logins: patient.mahmoud@ihis.com, patient.nadia@ihis.com, patient.ahmedz@ihis.com
and dr.omar@ihis.com — password 123456 (demo).
"""
import os
import shutil
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('FLASK_CONFIG', 'development')

from app import create_app, db  # noqa: E402

SRC = os.path.join(ROOT, 'AI apps')
CASES = [
    {
        'key': 'mahmoud', 'email': 'patient.mahmoud@ihis.com', 'username': 'patient.mahmoud',
        'name': 'Mahmoud Abdel-Rahman Ismail', 'gender': 'Male', 'age': 63, 'phone': '+20 100 000 1163',
        'src': 'C0394290.jpg.jpeg', 'image': 'head_ct_case1_mahmoud.jpg',
        'chronic': 'Hypertension (poorly compliant on amlodipine 10 mg); type 2 diabetes on metformin; ex-smoker',
        'problems': [('I10', 'Essential hypertension, poorly controlled', 'Moderate', 2200, 'On amlodipine 10 mg, poor compliance.'),
                     ('E11.9', 'Type 2 diabetes mellitus', 'Moderate', 1500, 'On metformin.'),
                     ('Z87.891', 'Personal history of nicotine dependence (ex-smoker)', 'Mild', 3000, '')],
        'vitals': {'blood_pressure_systolic': 168, 'blood_pressure_diastolic': 95, 'heart_rate': 78, 'temperature': 36.9,
                   'respiratory_rate': 16, 'oxygen_saturation': 97, 'blood_glucose': 142, 'pain_score': 6},
        'cc': 'Two days of headache and one episode of vomiting this morning.',
        'hpi': ('Brought to the ED by family. No trauma, no seizure, no focal weakness. GCS 15. '
                'BP 168/95, HR 78, afebrile, random blood sugar 142 mg/dL.'),
        'history': 'Known hypertensive on amlodipine 10 mg, poorly compliant. Type 2 diabetes on metformin. Ex-smoker.',
        'indication': 'Headache with vomiting in an uncontrolled hypertensive — exclude intracranial haemorrhage.',
        'diagnosis': 'Headache with vomiting in uncontrolled hypertension — intracranial haemorrhage to be excluded',
        'plan': 'Urgent non-contrast CT head. Blood pressure control, antiemetic, neurological observations. '
                'Disposition after imaging.',
    },
    {
        'key': 'nadia', 'email': 'patient.nadia@ihis.com', 'username': 'patient.nadia',
        'name': 'Nadia Fouad El-Sayed', 'gender': 'Female', 'age': 58, 'phone': '+20 100 000 1158',
        'src': 'WhatsApp Image 2026-09-11 at 1.01.09 PM.jpeg', 'image': 'head_ct_case2_nadia.jpg',
        'chronic': 'Hypertension for 12 years, irregular follow-up. No anticoagulants.',
        'problems': [('I10', 'Essential hypertension (12 years, irregular follow-up)', 'Severe', 4380, ''),
                     ('R47.1', 'Dysarthria (acute)', 'Severe', 0, 'Onset ~90 min before arrival.'),
                     ('G81.91', 'Right hemiparesis (acute), 2/5', 'Severe', 0, 'Acute stroke protocol.')],
        'vitals': {'blood_pressure_systolic': 205, 'blood_pressure_diastolic': 110, 'heart_rate': 92, 'temperature': 37.0,
                   'respiratory_rate': 18, 'oxygen_saturation': 96, 'pain_score': 3},
        'cc': 'Sudden onset right-sided weakness and slurred speech while cooking, about 90 minutes before arrival.',
        'hpi': ('Vomited twice en route. GCS 13 (E3 V4 M6). BP 205/110, HR 92. Right hemiparesis 2/5, right facial '
                'droop, no neck stiffness.'),
        'history': 'Hypertension for 12 years with irregular follow-up. No anticoagulants.',
        'indication': 'Acute stroke protocol — differentiate ischaemic from haemorrhagic stroke before the thrombolysis decision.',
        'diagnosis': 'Acute stroke syndrome (right hemiparesis, dysarthria) — haemorrhage vs ischaemia to be determined',
        'plan': 'Stroke protocol activated: immediate non-contrast CT head, stroke team notified, blood pressure '
                'management, NPO, thrombolysis decision after imaging.',
    },
    {
        'key': 'ahmedz', 'email': 'patient.ahmedz@ihis.com', 'username': 'patient.ahmedz',
        'name': 'Ahmed Samir Zaki', 'gender': 'Male', 'age': 34, 'phone': '+20 100 000 1134',
        'src': 'sdh-13-02.jpg.jpeg', 'image': 'head_ct_case3_ahmed.jpg',
        'chronic': 'None',
        'problems': [('S09.90XA', 'Head injury after motorcycle fall (no helmet), left side', 'Severe', 0,
                      'Brief loss of consciousness, lucid interval, now increasingly drowsy; GCS 13 and declining.')],
        'vitals': {'blood_pressure_systolic': 138, 'blood_pressure_diastolic': 84, 'heart_rate': 62, 'temperature': 36.8,
                   'respiratory_rate': 14, 'oxygen_saturation': 98, 'pain_score': 5},
        'cc': 'Fell from a motorcycle onto the left side of the head about 4 hours ago, no helmet.',
        'hpi': ('Brief loss of consciousness at the scene, then lucid, now increasingly drowsy. GCS 13 and declining. '
                'BP 138/84, HR 62. Left temporal scalp swelling; pupils equal and reactive.'),
        'history': 'No medical history, no medications.',
        'indication': 'Head trauma with loss of consciousness and deteriorating GCS.',
        'diagnosis': 'Closed head injury with lucid interval and declining GCS — intracranial haematoma suspected',
        'plan': 'Immediate non-contrast CT head, cervical spine precautions, neurosurgical referral on standby, '
                'hourly GCS and pupils.',
    },
]


def main():
    app = create_app(os.environ.get('FLASK_CONFIG', 'development'))
    with app.app_context():
        if not app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite:'):
            raise SystemExit('Demo seeding is only allowed on a local SQLite database.')
        from flask_login import login_user
        from app.models import (Appointment, Department, Diagnosis, Doctor, ImagingType, MedicalRecord, Patient,
                                PatientDocument, Problem, RadiologyOrder, Role, Specialty, User, VitalSign)
        from app.services import clinical_orders
        from app.services.timeline import record_event
        from app.utils import assign_mrn, utcnow

        ed_dept = Department.query.filter_by(name='Emergency').first()
        ed_spec = Specialty.query.filter_by(name='Emergency Medicine').first()
        nurse = User.query.filter_by(email='nurse@ihis.com').first()

        # ---- Emergency physician (once) --------------------------------------
        ed_user = User.query.filter_by(email='dr.omar@ihis.com').first()
        if ed_user is None:
            ed_user = User(username='dr_omar', email='dr.omar@ihis.com', full_name='Dr. Omar Farouk',
                           user_type='doctor', department_id=ed_dept.id if ed_dept else None)
            ed_user.set_password('123456')
            ed_user.roles.append(Role.query.filter_by(name='Doctor').first())
            db.session.add(ed_user)
            db.session.flush()
            db.session.add(Doctor(user_id=ed_user.id, specialty_id=ed_spec.id if ed_spec else None,
                                  license_number=f'LIC-ED-{ed_user.id}', years_of_experience=9, consultation_fee=200.0))
            db.session.commit()
            print('created Emergency physician dr.omar@ihis.com')
        ed_doc = Doctor.query.filter_by(user_id=ed_user.id).first()
        ct = ImagingType.query.filter_by(name='CT Scan').first() or ImagingType.query.first()

        base = app.config.get('UPLOAD_FOLDER') or os.path.join(ROOT, 'var', 'uploads')
        demo_dir = os.path.join(ROOT, 'app', 'static', 'demo_images', 'head_ct')
        docs_dir = os.path.join(base, 'medical_documents')
        rad_dir = os.path.join(base, 'radiology_images')
        for d in (demo_dir, docs_dir, rad_dir):
            os.makedirs(d, exist_ok=True)

        now = utcnow()
        summary = []
        for i, c in enumerate(CASES):
            # images: a clean copy for the repo demo folder + private copies for chart and order
            src = os.path.join(SRC, c['src'])
            if not os.path.isfile(src):
                raise SystemExit(f'Image not found: {src}')
            demo_copy = os.path.join(demo_dir, c['image'])
            if not os.path.isfile(demo_copy):
                shutil.copyfile(src, demo_copy)
            doc_copy = os.path.join(docs_dir, 'demo_' + c['image'])
            if not os.path.isfile(doc_copy):
                shutil.copyfile(src, doc_copy)
            rad_copy = os.path.join(rad_dir, 'demo_' + c['image'])
            if not os.path.isfile(rad_copy):
                shutil.copyfile(src, rad_copy)

            user = User.query.filter_by(email=c['email']).first()
            if user is None:
                user = User(username=c['username'], email=c['email'], full_name=c['name'], user_type='patient',
                            phone=c['phone'])
                user.set_password('123456')
                user.roles.append(Role.query.filter_by(name='Patient').first())
                db.session.add(user)
                db.session.flush()
            patient = Patient.query.filter_by(user_id=user.id).first()
            if patient is None:
                dob = date(now.year - c['age'], 6, 15)
                patient = Patient(user_id=user.id, date_of_birth=dob, gender=c['gender'], phone=c['phone'],
                                  allergies='NKDA (No Known Drug Allergies)', chronic_diseases=c['chronic'],
                                  department_id=ed_dept.id if ed_dept else None)
                db.session.add(patient)
                db.session.flush()
                assign_mrn(patient)
                print(f'created patient {c["name"]} ({patient.mrn})')
            else:
                print(f'patient {c["name"]} exists (#{patient.id}); adding only what is missing')
            db.session.commit()

            with app.test_request_context():
                login_user(ed_user)
                arrived = now - timedelta(minutes=40 + 15 * i)

                if not Problem.query.filter_by(patient_id=patient.id).first():
                    for j, (code, desc, sev, days, notes) in enumerate(c['problems']):
                        db.session.add(Problem(patient_id=patient.id, icd10_code=code, description=desc, severity=sev,
                                               onset=(now - timedelta(days=days)).date(), status='Active', notes=notes,
                                               recorded_by=ed_user.id))
                        if j == 0:
                            db.session.add(Diagnosis(patient_id=patient.id, doctor_id=ed_doc.id, icd10_code=code,
                                                     description=desc, is_primary=True, notes=notes, date_diagnosed=arrived))

                if not VitalSign.query.filter_by(patient_id=patient.id).first():
                    db.session.add(VitalSign(patient_id=patient.id, nurse_id=nurse.id if nurse else None,
                                             recorded_at=arrived + timedelta(minutes=5), **c['vitals']))
                    v = c['vitals']
                    record_event(patient.id, 'VITALS', 'ED triage vitals recorded',
                                 f"BP {v['blood_pressure_systolic']}/{v['blood_pressure_diastolic']} · HR {v['heart_rate']} · "
                                 f"SpO2 {v['oxygen_saturation']}%", department='Emergency', occurred_at=arrived + timedelta(minutes=5))

                if not Appointment.query.filter_by(patient_id=patient.id, doctor_id=ed_doc.id).first():
                    db.session.add(Appointment(patient_id=patient.id, doctor_id=ed_doc.id, scheduled_at=arrived,
                                               duration_minutes=45, status='InConsultation', reason=c['cc'][:200],
                                               priority='Urgent', visit_type='WalkIn', queue_number=i + 1,
                                               checked_in_at=arrived, created_by=ed_user.id))

                if not MedicalRecord.query.filter_by(patient_id=patient.id).first():
                    db.session.add(MedicalRecord(
                        patient_id=patient.id, doctor_id=ed_doc.id, visit_date=arrived + timedelta(minutes=10),
                        diagnosis=c['diagnosis'], treatment_plan=c['plan'],
                        clinical_notes=(f"Chief complaint: {c['cc']}\nHPI / examination: {c['hpi']}\n"
                                        f"History: {c['history']}\nImaging indication: {c['indication']}"),
                        status='Signed', signed_by=ed_user.id, signed_at=arrived + timedelta(minutes=20)))
                    record_event(patient.id, 'ENCOUNTER', 'ED assessment signed', c['diagnosis'],
                                 department='Emergency', occurred_at=arrived + timedelta(minutes=20))

                order = RadiologyOrder.query.filter_by(patient_id=patient.id, imaging_type_id=ct.id).first()
                if order is None:
                    order = clinical_orders.create_radiology_order(
                        patient, ed_doc, ct.id, priority='Urgent',
                        notes=f"Non-contrast CT head. {c['indication']}", origin='Emergency department')
                    order.order_date = arrived + timedelta(minutes=15)
                    order.status = 'Performed'
                    order.image_urls = f'radiology_images/demo_{c["image"]}'
                    record_event(patient.id, 'RADIOLOGY', 'CT head performed — awaiting report',
                                 f'Order #{order.id} · urgent · image acquired', source_type='radiology_order',
                                 source_id=order.id, department='Radiology', occurred_at=arrived + timedelta(minutes=35))
                    print(f'  CT order #{order.id} created (Performed, image attached)')

                rel = f'medical_documents/demo_{c["image"]}'
                doc = PatientDocument.query.filter_by(patient_id=patient.id, file_url=rel).first()
                if doc is None:
                    doc = PatientDocument(patient_id=patient.id, title=f'Non-contrast CT head — axial slice (order #{order.id})',
                                          file_url=rel, document_type='clinical_image', uploaded_by=ed_user.id,
                                          category='imaging',
                                          notes=f"{c['indication']} JPEG export of one axial slice (not DICOM).",
                                          uploaded_at=arrived + timedelta(minutes=35))
                    db.session.add(doc)
                    db.session.flush()
                db.session.commit()
            summary.append((patient, order, doc))

        print()
        for patient, order, doc in summary:
            print(f"{patient.user.full_name:30} {patient.mrn}  CT order #{order.id} ({order.status})  doc #{doc.id}"
                  f"  -> /ai/ich-detection?doc={doc.id}")
        print('Radiologist: radio@ihis.com / 123456 · ED physician: dr.omar@ihis.com / 123456 (Emergency Medicine)')


if __name__ == '__main__':
    main()
