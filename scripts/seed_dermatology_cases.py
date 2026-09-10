"""Create the two Dermatology outpatient cases prepared by Dr. Amira (idempotent).

    python scripts/seed_dermatology_cases.py

Case 1 — A.K., 58 M, site engineer (years of sun exposure), changing mole on the
left shoulder for 4 months, ABCDE positive (asymmetric, ragged border, colour
variegation, 9 mm, evolving), father treated for BCC, severe sunburns in youth.
Case 2 — W.S., 34 M, accountant, routine annual skin check, long-standing 4 mm
uniform light-brown mole on the lower back, ABCDE negative, no family history.

Both are linked to Dr. Amira (dr.amira@ihis.com, Dermatology): registered under
the Dermatology department, seen in her clinic today (appointments), documented
in a signed encounter with the ABCDE findings, and each has a real dermoscopic
image (ISIC archive, CC-0) attached to the chart.

The skin-lesion AI (ResNet-50 + EfficientNet-B0 ensemble, local) is then run on
each image through the same code path as the page. The model's own output
decides what happens next — nothing is faked:
  * case 1 -> melanoma call -> CRITICAL alert assigned to Dr. Amira, urgent
    task, notification, plus her orders: excisional biopsy (pathology lab order,
    urgent) and a referral for regional lymph-node examination;
  * case 2 -> nevus call -> no alert; the prediction and image stay in the
    chart as the baseline, with a 12-month follow-up scheduled.

Logins: patient.ak@ihis.com / patient.ws@ihis.com — password 123456 (demo).
"""
import io
import os
import shutil
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('FLASK_CONFIG', 'development')

from app import create_app, db  # noqa: E402

CASES = [
    {
        'key': 'ak', 'email': 'patient.ak@ihis.com', 'username': 'patient.ak', 'name': 'A. K.',
        'mrn': 'MRN-104592', 'dob': date(1968, 5, 20), 'gender': 'Male', 'phone': '+20 100 000 4592',
        'image': 'derm_case1_melanoma.jpg', 'image_title': 'Dermoscopy — left shoulder lesion (9 mm)',
        'occupation': 'Site engineer — prolonged direct sun exposure for years',
        'cc': 'Significant change in the shape and size of a mole on the left shoulder over the past four months.',
        'hpi': ('Painless lesion, recently associated with intermittent itching. ABCDE: Asymmetry — irregular, right half '
                'distinctly different from the left. Border — ragged, notched, poorly defined, blending into surrounding '
                'skin. Colour — marked variegation: dark brown, black and small blue/grey areas. Diameter — ~9 mm (> 6 mm). '
                'Evolving — previously flat and small; now elevated and gradually expanding.'),
        'history': ('No chronic illness or immunodeficiency. Family history: father treated for basal cell carcinoma. '
                    'Personal history of severe, repeated sunburns in youth.'),
        'diagnosis': 'Changing pigmented lesion, left shoulder — clinically suspicious for melanoma (ABCDE positive)',
        'plan': ('Dermoscopy performed and image analysed with the skin-lesion AI (decision support). Excisional '
                 'biopsy with 2 mm margin ordered for histopathological confirmation. Referral for comprehensive '
                 'regional lymph-node examination. Sun-protection counselling; review with histology.'),
        'problems': [('D48.5', 'Pigmented skin lesion of uncertain behaviour, left shoulder (suspected melanoma)', 'Severe', 120,
                      'ABCDE positive; 9 mm; evolving over 4 months.'),
                     ('Z80.8', 'Family history of skin cancer (father: basal cell carcinoma)', 'Mild', 3650, ''),
                     ('Z92.89', 'History of severe repeated sunburns; occupational sun exposure', 'Mild', 7300, '')],
        'appointment_reason': 'Changing mole, left shoulder — dermoscopy',
    },
    {
        'key': 'ws', 'email': 'patient.ws@ihis.com', 'username': 'patient.ws', 'name': 'W. S.',
        'mrn': 'MRN-104822', 'dob': date(1992, 2, 11), 'gender': 'Male', 'phone': '+20 100 000 4822',
        'image': 'derm_case2_nevus.jpg', 'image_title': 'Dermoscopy — lower back mole (4 mm), baseline',
        'occupation': 'Accountant — indoor work, minimal occupational sun exposure',
        'cc': 'Routine annual skin screening; asks to check a long-standing mole on the lower back "to be safe".',
        'hpi': ('Completely asymptomatic (no itching, bleeding or pain). ABCDE: Asymmetry — perfectly symmetrical. '
                'Border — regular, smooth, sharply defined. Colour — uniform solid light brown. Diameter — ~4 mm '
                '(< 6 mm). Evolving — present since childhood with no change in size, shape or colour.'),
        'history': 'No chronic illness. Family history negative for melanoma or other skin cancers. No severe sunburns, no tanning beds.',
        'diagnosis': 'Benign melanocytic nevus, lower back — stable, ABCDE negative',
        'plan': ('Dermoscopy performed; image and AI prediction logged in the chart as baseline for future comparison. '
                 'Patient reassured about the benign nature of the lesion. No biopsy or surgical intervention. Routine '
                 'follow-up skin check in 12 months; return earlier if any change.'),
        'problems': [('D22.5', 'Melanocytic nevus of trunk (lower back), stable since childhood', 'Mild', 9000,
                      'ABCDE negative; 4 mm; uniform colour.')],
        'appointment_reason': 'Annual skin screening — long-standing mole, lower back',
    },
]


def main():
    app = create_app(os.environ.get('FLASK_CONFIG', 'development'))
    with app.app_context():
        if not app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite:'):
            raise SystemExit('Demo seeding is only allowed on a local SQLite database.')
        from flask_login import login_user
        from werkzeug.datastructures import FileStorage
        from app.models import (Appointment, Department, Diagnosis, Doctor, FollowUp, LabOrder, LabTestCatalog,
                                MedicalRecord, Patient, PatientDocument, Problem, Role, User)
        from app.services import clinical_orders
        from app.services.timeline import record_event
        from app.utils import utcnow

        derm_user = User.query.filter_by(email='dr.amira@ihis.com').first()
        derm = Doctor.query.filter_by(user_id=derm_user.id).first() if derm_user else None
        if derm is None:
            raise SystemExit('Run python seed.py first (dr.amira@ihis.com missing).')
        dept = Department.query.filter_by(name='Dermatology').first()
        now = utcnow()
        today9 = now.replace(hour=9, minute=0, second=0, microsecond=0)

        base = app.config.get('UPLOAD_FOLDER') or os.path.join(ROOT, 'var', 'uploads')
        dest_dir = os.path.join(base, 'medical_documents')
        os.makedirs(dest_dir, exist_ok=True)

        results = []
        for i, c in enumerate(CASES):
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
                patient = Patient(user_id=user.id, mrn=c['mrn'], date_of_birth=c['dob'], gender=c['gender'],
                                  phone=c['phone'], allergies='NKDA (No Known Drug Allergies)',
                                  chronic_diseases='None', department_id=dept.id if dept else None,
                                  address=c['occupation'])
                db.session.add(patient)
                db.session.flush()
                print(f'created patient {c["name"]} ({c["mrn"]})')
            else:
                print(f'patient {c["name"]} already exists (#{patient.id}); adding only what is missing')
            db.session.commit()

            with app.test_request_context():
                login_user(derm_user)
                visit_at = today9 + timedelta(minutes=30 * i)

                # Outpatient clinic appointment with Dr. Amira (today)
                if not Appointment.query.filter_by(patient_id=patient.id, doctor_id=derm.id).first():
                    db.session.add(Appointment(patient_id=patient.id, doctor_id=derm.id, scheduled_at=visit_at,
                                               duration_minutes=30, status='Completed', reason=c['appointment_reason'],
                                               visit_type='Scheduled', queue_number=i + 1,
                                               checked_in_at=visit_at - timedelta(minutes=10),
                                               created_by=derm_user.id))

                # Problem list + encounter diagnosis
                if not Problem.query.filter_by(patient_id=patient.id).first():
                    for j, (code, desc, sev, days, notes) in enumerate(c['problems']):
                        db.session.add(Problem(patient_id=patient.id, icd10_code=code, description=desc, severity=sev,
                                               onset=(now - timedelta(days=days)).date(), status='Active', notes=notes,
                                               recorded_by=derm_user.id))
                        if j == 0:
                            db.session.add(Diagnosis(patient_id=patient.id, doctor_id=derm.id, icd10_code=code,
                                                     description=desc, is_primary=True, notes=notes,
                                                     date_diagnosed=visit_at))

                # Signed clinic note with the ABCDE documentation
                if not MedicalRecord.query.filter_by(patient_id=patient.id).first():
                    db.session.add(MedicalRecord(
                        patient_id=patient.id, doctor_id=derm.id, visit_date=visit_at,
                        diagnosis=c['diagnosis'], treatment_plan=c['plan'],
                        clinical_notes=(f"Occupation / exposure: {c['occupation']}.\nChief complaint: {c['cc']}\n"
                                        f"HPI: {c['hpi']}\nMedical & family history: {c['history']}"),
                        status='Signed', signed_by=derm_user.id, signed_at=visit_at + timedelta(minutes=20)))
                    record_event(patient.id, 'ENCOUNTER', 'Dermatology clinic note signed', c['diagnosis'],
                                 department='Dermatology', occurred_at=visit_at + timedelta(minutes=20))

                # Dermoscopic image attached to the chart
                src = os.path.join(ROOT, 'app', 'static', 'demo_images', c['image'])
                dst = os.path.join(dest_dir, 'demo_' + c['image'])
                if not os.path.isfile(dst):
                    shutil.copyfile(src, dst)
                rel = f'medical_documents/demo_{c["image"]}'
                doc = PatientDocument.query.filter_by(patient_id=patient.id, file_url=rel).first()
                if doc is None:
                    doc = PatientDocument(patient_id=patient.id, title=c['image_title'], file_url=rel,
                                          document_type='clinical_image', uploaded_by=derm_user.id,
                                          category='dermoscopy',
                                          notes='Dermoscopic image (ISIC archive, CC-0) — demo case prepared by Dr. Amira.',
                                          uploaded_at=visit_at + timedelta(minutes=5))
                    db.session.add(doc)
                    db.session.flush()
                db.session.commit()

                # Run the real skin-lesion AI through the page's code path (alert engine included)
                from app.services.ai.skin_lesion_classification import classify_skin_lesion, skin_model_available
                from app.services.ai import platform as _platform
                from app.routes.ai import _alert_on_positive_finding
                from app.models import AIUsageLog
                already = AIUsageLog.query.filter_by(patient_id=patient.id, feature='dermatology.classify').first() \
                    if hasattr(AIUsageLog, 'feature') else None
                if not skin_model_available():
                    print('  ! skin model not installed: AI step skipped')
                    result = None
                elif already is not None:
                    print('  AI already run for this patient; skipping')
                    result = None
                else:
                    with open(dst, 'rb') as f:
                        fs = FileStorage(stream=io.BytesIO(f.read()), filename=os.path.basename(dst),
                                         content_type='image/jpeg')
                    result = classify_skin_lesion(fs)
                    if 'error' in result:
                        print('  ! AI error:', result['error'])
                    else:
                        result['usage_id'] = _platform.record_usage(
                            'dermatology.classify', 'ok', provider='local', patient_id=patient.id,
                            detail=f"{'melanoma' if result.get('is_melanoma') else 'nevus'} {result.get('percent')}%")
                        _alert_on_positive_finding(result, patient, doc)
                        record_event(patient.id, 'AI', f"Skin-lesion AI: {result['prediction']} ({result['percent']}%)",
                                     'ResNet-50 + EfficientNet-B0 ensemble on the dermoscopic image; Grad-CAM saved.',
                                     source_type='ai_tool', source_id=doc.id, department='Dermatology',
                                     occurred_at=visit_at + timedelta(minutes=8))
                        print(f"  AI: {result['prediction']} p_mel={result['proba_melanoma']} heatmap={'yes' if result.get('heatmap_key') else 'no'}")
                db.session.commit()

                # Dr. Amira's orders / plan
                if c['key'] == 'ak':
                    biopsy = LabTestCatalog.query.filter_by(test_name='Pathology Biopsy').first()
                    if biopsy and not LabOrder.query.filter_by(patient_id=patient.id, test_id=biopsy.id).first():
                        clinical_orders.create_lab_order(
                            patient, derm, biopsy.id, priority='Urgent',
                            notes='Excisional biopsy, left shoulder pigmented lesion (9 mm, ABCDE positive) — '
                                  'histopathology to confirm/exclude melanoma; report Breslow thickness if positive.')
                        print('  ordered excisional biopsy (pathology)')
                    from app.models import Referral
                    if not Referral.query.filter_by(patient_id=patient.id).first():
                        clinical_orders.create_referral(
                            patient, derm,
                            'Suspected melanoma, left shoulder (AI-flagged, ABCDE positive). Please perform a comprehensive '
                            'clinical examination of the regional (axillary and cervical) lymph nodes.',
                            to_specialty='Surgery', urgency='Urgent', origin='Dermatology clinic')
                        print('  referral for regional lymph-node examination sent')
                else:
                    if not FollowUp.query.filter_by(patient_id=patient.id).first():
                        db.session.add(FollowUp(patient_id=patient.id, provider_id=derm.id,
                                                scheduled_for=visit_at + timedelta(days=365),
                                                reason='Routine annual skin check — compare lower-back nevus with baseline dermoscopy',
                                                status='Scheduled', created_by=derm_user.id))
                        db.session.add(Appointment(patient_id=patient.id, doctor_id=derm.id,
                                                   scheduled_at=visit_at + timedelta(days=365), duration_minutes=20,
                                                   status='Scheduled', reason='Annual skin check (12-month follow-up)',
                                                   visit_type='Follow-up', created_by=derm_user.id))
                        print('  12-month follow-up scheduled')
                db.session.commit()
            results.append((patient, doc))

        from app.models import ClinicalAlert, Task
        print()
        for patient, doc in results:
            alerts = ClinicalAlert.query.filter_by(patient_id=patient.id, status='OPEN').all()
            tasks = Task.query.filter_by(patient_id=patient.id).all()
            print(f"{patient.user.full_name} {patient.mrn}: login {patient.user.email} / 123456 · doc #{doc.id} · "
                  f"alerts: {[(a.severity, a.alert_type, 'assigned' if a.assigned_to else 'unassigned') for a in alerts]} · tasks: {len(tasks)}")
        print("Dermatologist: dr.amira@ihis.com / 123456 -> dashboard (critical tile), /ai/skin-lesion-detection?doc=<id>")


if __name__ == '__main__':
    main()
