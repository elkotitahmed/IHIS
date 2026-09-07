"""Stage the judging walkthrough on the development database (idempotent).

* Demo Patient (patient@ihis.com) keeps their penicillin allergy and gets the
  demo images attached as clinical documents (chest X-ray, leg X-ray, skin
  lesion) so every image-AI page can be opened with one click.
* A chest X-ray order with an UNSIGNED report reading "Large right
  pneumothorax" is created (or re-opened) so the radiologist can sign it live
  and the critical-finding engine fires in front of the audience.

    python scripts/demo_reset.py
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('FLASK_CONFIG', 'development')

from app import create_app, db  # noqa: E402

DEMO_IMAGES = [
    ('chest_pneumothorax.jpg', 'Chest X-ray (PA) — demo', 'clinical_image'),
    ('leg_fracture.jpg', 'Leg X-ray — demo', 'clinical_image'),
    ('skin_lesion.jpg', 'Skin lesion photo — demo', 'clinical_image'),
]
REPORT_FINDINGS = ('Large right-sided pneumothorax with visible visceral pleural line and absent lung markings '
                   'laterally. Mild mediastinal shift to the left. No pleural effusion. Heart size normal.')
REPORT_IMPRESSION = 'Large right pneumothorax with early tension features — urgent clinical correlation.'


def main():
    app = create_app('development')
    with app.app_context():
        from app.models import (Allergy, Doctor, ImagingType, Patient, PatientDocument, RadiologyOrder,
                                RadiologyReport, User)
        pu = User.query.filter_by(email='patient@ihis.com').first()
        patient = Patient.query.filter_by(user_id=pu.id).first() if pu else None
        if patient is None:
            raise SystemExit('Run python seed.py first (patient@ihis.com missing).')
        doctor = Doctor.query.join(User, Doctor.user_id == User.id).filter(User.email == 'dr.ahmed@ihis.com').first()
        radio = User.query.filter_by(email='radio@ihis.com').first()

        # 1. allergy on record
        if not Allergy.query.filter_by(patient_id=patient.id).filter(Allergy.substance.ilike('%penicillin%')).first():
            db.session.add(Allergy(patient_id=patient.id, substance='Penicillin', reaction='Rash and hives',
                                   severity='Severe', status='Active'))
            print('added penicillin allergy')

        # 2. demo documents
        base = app.config.get('UPLOAD_FOLDER') or os.path.join(ROOT, 'var', 'uploads')
        dest_dir = os.path.join(base, 'medical_documents')
        os.makedirs(dest_dir, exist_ok=True)
        for fname, title, dtype in DEMO_IMAGES:
            src = os.path.join(ROOT, 'app', 'static', 'demo_images', fname)
            dst = os.path.join(dest_dir, 'demo_' + fname)
            if not os.path.isfile(dst):
                shutil.copyfile(src, dst)
            rel = f'medical_documents/demo_{fname}'
            if not PatientDocument.query.filter_by(patient_id=patient.id, file_url=rel).first():
                db.session.add(PatientDocument(patient_id=patient.id, title=title, file_url=rel, document_type=dtype,
                                               uploaded_by=pu.id, category='imaging', notes='Demo image (public / synthetic).'))
                print('attached', fname)

        # 3. chest X-ray order with an unsigned critical report, ready to sign live
        xray = ImagingType.query.filter_by(name='X-Ray').first() or ImagingType.query.first()
        order = (RadiologyOrder.query.join(RadiologyReport, RadiologyReport.order_id == RadiologyOrder.id)
                 .filter(RadiologyOrder.patient_id == patient.id, RadiologyReport.impression == REPORT_IMPRESSION).first())
        if order is None:
            order = RadiologyOrder(patient_id=patient.id, doctor_id=doctor.id if doctor else None,
                                   imaging_type_id=xray.id, status='Performed', priority='Urgent',
                                   notes='Demo: acute chest pain and dyspnoea after a fall.')
            db.session.add(order); db.session.flush()
            db.session.add(RadiologyReport(order_id=order.id, findings=REPORT_FINDINGS, impression=REPORT_IMPRESSION,
                                           recommendation='Immediate clinical assessment; consider chest drain.',
                                           reported_by=radio.id if radio else None, status='Draft'))
            print('created chest X-ray order', order.id, 'with an unsigned pneumothorax report')
        else:
            rep = RadiologyReport.query.filter_by(order_id=order.id).first()
            if rep and rep.status != 'Draft':
                rep.status = 'Draft'; rep.signed_by = None; order.status = 'Performed'
                print('re-opened report on order', order.id, 'for the live signing step')
            else:
                print('chest X-ray order', order.id, 'already staged')
        db.session.commit()
        print('\nDemo staged. Radiologist: /radiology/orders/%d/report -> Sign. Physician: red banner -> alert.' % order.id)


if __name__ == '__main__':
    main()
