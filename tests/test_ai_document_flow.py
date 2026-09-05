"""Regression tests: AI image tools analysing a patient-uploaded document.

A patient uploads an image under their Medical Documents; a clinician (with
documented need-to-know access to the patient) opens the AI tool with that
document pre-loaded (``?doc=<id>``) and analyses it. Nothing leaks across
patients and unsupported document types are rejected gracefully.
"""
import datetime
import os
import unittest
from unittest import mock

from app import create_app, db, bcrypt
from app.models import (User, Role, Patient, Doctor, PatientDocument,
                        ClinicalAlert)


def make_user(username, role_name, password='Tests@12345'):
    role = Role.query.filter_by(name=role_name).first()
    user_type = 'admin' if role_name in ('Admin', 'SuperAdmin') else 'staff'
    u = User(
        username=username,
        email=f'{username}@example.com',
        full_name=f'{role_name} User',
        user_type=user_type,
        password_hash=bcrypt.generate_password_hash(password).decode('utf-8'),
        is_active=True,
    )
    if role:
        u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u, password


class AIDocumentFlowTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for name in ('Doctor', 'Radiologist', 'Dentist', 'Nurse', 'Patient',
                     'Admin', 'SuperAdmin', 'Pharmacist'):
            if not Role.query.filter_by(name=name).first():
                db.session.add(Role(name=name))
        db.session.commit()
        from app.permissions import seed_permissions
        seed_permissions(db)
        self.client = self.app.test_client()
        self._cleanup_files = []

    def tearDown(self):
        for path in self._cleanup_files:
            try:
                os.remove(path)
            except OSError:
                pass
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, username, password):
        u = User.query.filter_by(username=username).first()
        return self.client.post('/auth/login', data={
            'email': u.email, 'password': password})

    def _make_patient_with_doc(self, filename='lesion.jpg', ext='.jpg'):
        pat_user, _ = make_user('the_patient', 'Patient')
        patient = Patient(
            mrn='AIDOC001',
            user_id=pat_user.id,
            date_of_birth=datetime.date(1990, 1, 1),
            gender='Female',
        )
        db.session.add(patient)
        db.session.commit()

        doc_dir = os.path.join(self.app.config['UPLOAD_FOLDER'],
                               'medical_documents')
        os.makedirs(doc_dir, exist_ok=True)
        path = os.path.join(doc_dir, filename)
        with open(path, 'wb') as f:
            f.write(b'\xff\xd8\xff\xe0' + b'\x00' * 256)
        self._cleanup_files.append(path)

        doc = PatientDocument(
            patient_id=patient.id,
            title='Uploaded from patient',
            document_type='imaging',
            file_url=f'medical_documents/{filename}',
            uploaded_by=pat_user.id,
        )
        db.session.add(doc)
        db.session.commit()
        return patient, doc

    def _doctor_with_access(self, patient):
        doc_user, pw = make_user('attending_doc', 'Doctor')
        doctor = Doctor(user_id=doc_user.id, license_number='LIC-1')
        db.session.add(doctor)
        db.session.commit()
        from app.models import Appointment
        db.session.add(Appointment(patient_id=patient.id, doctor_id=doctor.id,
                                   scheduled_at=datetime.datetime.utcnow()))
        db.session.commit()
        return doc_user.username, pw

    def test_authorized_doctor_sees_selected_patient_image(self):
        patient, doc = self._make_patient_with_doc()
        uname, pw = self._doctor_with_access(patient)
        self._login(uname, pw)
        resp = self.client.get(f'/ai/skin-lesion-detection?doc={doc.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Selected Patient Image', resp.data)

    def test_post_analyses_the_patient_document(self):
        patient, doc = self._make_patient_with_doc('from_patient.png', '.png')
        uname, pw = self._doctor_with_access(patient)
        self._login(uname, pw)
        with mock.patch('app.services.ai.skin_lesion_classification.'
                        'classify_skin_lesion',
                        return_value={
                            'feature': 'skin',
                            'orig_key': 'abc.jpg',
                            'heatmap_key': None,
                            'prediction': 'Nevus',
                            'is_melanoma': False,
                            'percent': 88,
                            'confidence': 0.88,
                            'proba_melanoma': 0.12,
                            'proba_nevus': 0.88,
                            'per_model': [],
                            'models_used': ['resnet50_best.pth'],
                        }):
            resp = self.client.post('/ai/skin-lesion-detection', data={
                'doc': str(doc.id)})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Nevus', resp.data)

    def test_post_doc_run_uses_the_document_file(self):
        patient, doc = self._make_patient_with_doc()
        uname, pw = self._doctor_with_access(patient)
        self._login(uname, pw)
        seen = {}

        def fake_classify(storage):
            seen['filename'] = storage.filename
            return {
                'feature': 'skin', 'orig_key': 'a.jpg', 'heatmap_key': None,
                'prediction': 'Nevus', 'is_melanoma': False, 'percent': 50,
                'confidence': 0.5, 'proba_melanoma': 0.1, 'proba_nevus': 0.5,
                'per_model': [], 'models_used': ['x'],
            }

        with mock.patch('app.services.ai.skin_lesion_classification.'
                        'classify_skin_lesion', side_effect=fake_classify):
            resp = self.client.post('/ai/skin-lesion-detection', data={
                'doc': str(doc.id)})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(seen['filename'], 'lesion.jpg')

    def test_doctor_without_access_cannot_analyse_other_patient_doc(self):
        patient, doc = self._make_patient_with_doc()
        stranger_user, pw = make_user('stranger_doc', 'Doctor')
        db.session.add(Doctor(user_id=stranger_user.id, license_number='LIC-2'))
        db.session.commit()
        self._login('stranger_doc', pw)
        resp = self.client.post('/ai/skin-lesion-detection', data={
            'doc': str(doc.id)})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'not available to you', resp.data)

    def test_non_image_document_rejected(self):
        patient, doc = self._make_patient_with_doc('notes.pdf', '.pdf')
        uname, pw = self._doctor_with_access(patient)
        self._login(uname, pw)
        resp = self.client.post('/ai/skin-lesion-detection', data={
            'doc': str(doc.id)})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'not a supported image', resp.data)

    def _superadmin(self):
        u, pw = make_user('boss_admin', 'SuperAdmin')
        return u.username, pw

    def test_tooth_and_fracture_pages_show_selected_image(self):
        patient, doc = self._make_patient_with_doc('panoramic.jpg')
        uname, pw = self._superadmin()
        self._login(uname, pw)
        for endpoint in ('tooth-segmentation', 'fracture-detection'):
            resp = self.client.get(f'/ai/{endpoint}?doc={doc.id}')
            self.assertEqual(resp.status_code, 200, endpoint)
            self.assertIn(b'Selected Patient Image', resp.data)

    def test_patient_360_renders_documents_with_ai_actions(self):
        patient, doc = self._make_patient_with_doc('from_patient.png', '.png')
        uname, pw = self._superadmin()
        self._login(uname, pw)
        resp = self.client.get(f'/clinical/patient/{patient.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Analyze', resp.data)
        self.assertIn(b'<img', resp.data)
        self.assertIn(b'/ai/skin-lesion-detection', resp.data)
        self.assertIn(f'doc={doc.id}'.encode(), resp.data)

    def _alert(self, patient_id, alert_type):
        return ClinicalAlert.query.filter_by(
            patient_id=patient_id, alert_type=alert_type).first()

    def test_positive_fracture_raises_open_alert(self):
        patient, doc = self._make_patient_with_doc('xray.jpg')
        uname, pw = self._doctor_with_access(patient)
        self._login(uname, pw)
        with mock.patch(
                'app.services.ai.fracture_detection.detect_fracture',
                return_value={
                    'feature': 'fracture', 'detected': True,
                    'detections': [{'label': 'Fracture', 'confidence': 0.91,
                                    'percent': 91}],
                    'count_by_class': {'Fracture': 1}, 'avg_confidence': 91,
                    'orig_key': 'x.jpg', 'result_key': 'r.jpg',
                }):
            resp = self.client.post('/ai/fracture-detection', data={
                'doc': str(doc.id)})
        self.assertEqual(resp.status_code, 200)
        alert = self._alert(patient.id, 'AI_FRACTURE_DETECTED')
        self.assertIsNotNone(alert)
        self.assertEqual(alert.status, 'OPEN')
        self.assertIn('fracture', alert.message.lower())

    def test_positive_melanoma_raises_open_alert(self):
        patient, doc = self._make_patient_with_doc('lesion.jpg')
        uname, pw = self._doctor_with_access(patient)
        self._login(uname, pw)
        with mock.patch(
                'app.services.ai.skin_lesion_classification.classify_skin_lesion',
                return_value={
                    'feature': 'skin', 'orig_key': 'a.jpg', 'heatmap_key': None,
                    'prediction': 'Melanoma', 'is_melanoma': True, 'percent': 92,
                    'confidence': 0.92, 'proba_melanoma': 0.92,
                    'proba_nevus': 0.08, 'per_model': [], 'models_used': ['x'],
                }):
            resp = self.client.post('/ai/skin-lesion-detection', data={
                'doc': str(doc.id)})
        self.assertEqual(resp.status_code, 200)
        alert = self._alert(patient.id, 'AI_MELANOMA_SUSPECTED')
        self.assertIsNotNone(alert)
        self.assertEqual(alert.status, 'OPEN')

    def test_negative_finding_raises_no_alert(self):
        patient, doc = self._make_patient_with_doc('lesion.jpg')
        uname, pw = self._doctor_with_access(patient)
        self._login(uname, pw)
        with mock.patch(
                'app.services.ai.skin_lesion_classification.classify_skin_lesion',
                return_value={
                    'feature': 'skin', 'orig_key': 'a.jpg', 'heatmap_key': None,
                    'prediction': 'Nevus', 'is_melanoma': False, 'percent': 88,
                    'confidence': 0.88, 'proba_melanoma': 0.12,
                    'proba_nevus': 0.88, 'per_model': [], 'models_used': ['x'],
                }):
            resp = self.client.post('/ai/skin-lesion-detection', data={
                'doc': str(doc.id)})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self._alert(patient.id, 'AI_MELANOMA_SUSPECTED'))

    def test_repeated_positive_run_does_not_duplicate_alert(self):
        patient, doc = self._make_patient_with_doc('xray.jpg')
        uname, pw = self._doctor_with_access(patient)
        self._login(uname, pw)
        payload = {
            'feature': 'fracture', 'detected': True,
            'detections': [{'label': 'Fracture', 'confidence': 0.9,
                            'percent': 90}],
            'count_by_class': {'Fracture': 1}, 'avg_confidence': 90,
            'orig_key': 'x.jpg', 'result_key': 'r.jpg',
        }
        with mock.patch('app.services.ai.fracture_detection.detect_fracture',
                        return_value=payload):
            self.client.post('/ai/fracture-detection', data={'doc': str(doc.id)})
            self.client.post('/ai/fracture-detection', data={'doc': str(doc.id)})
        self.assertEqual(
            ClinicalAlert.query.filter_by(
                patient_id=patient.id,
                alert_type='AI_FRACTURE_DETECTED').count(),
            1)


if __name__ == '__main__':
    unittest.main()