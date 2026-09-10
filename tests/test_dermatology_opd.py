"""Dermatology outpatient flow: confident melanoma calls become CRITICAL alerts
assigned to the treating dermatologist with an urgent task; nevus calls raise
nothing; the hospital dashboard and patient list split inpatients / outpatients.
The classifier is mocked (no model weights, no network)."""
import io
import unittest
from datetime import date, datetime
from unittest import mock

from app import create_app, db
from app.models import (Admission, Appointment, Bed, ClinicalAlert, Doctor, Patient, PatientDocument, Role,
                        Specialty, Task, User, Ward)
from app.permissions import seed_permissions
from seed import ROLES


def _skin_result(is_mel, prob):
    return {'feature': 'skin', 'orig_key': 'x.jpg', 'prediction': 'Melanoma' if is_mel else 'Nevus',
            'label': 'Melanoma' if is_mel else 'Nevus', 'is_melanoma': is_mel, 'proba_melanoma': prob,
            'proba_nevus': 1 - prob, 'confidence': prob if is_mel else 1 - prob,
            'percent': int((prob if is_mel else 1 - prob) * 100), 'per_model': [], 'models_used': ['resnet50'],
            'heatmap_key': None}


class Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context(); self.ctx.push()
        db.create_all()
        for n in ROLES:
            db.session.add(Role(name=n))
        db.session.commit()
        seed_permissions(db)
        self.client = self.app.test_client()
        spec = Specialty(name='Dermatology'); db.session.add(spec); db.session.flush()
        self.derm_user = self._user('amira', 'Doctor')
        self.derm = Doctor(user_id=self.derm_user.id, specialty_id=spec.id); db.session.add(self.derm)
        self.p1 = self._patient('ak'); self.p2 = self._patient('ws')
        for p in (self.p1, self.p2):
            db.session.add(Appointment(patient_id=p.id, doctor_id=self.derm.id, scheduled_at=datetime.utcnow(),
                                       status='Completed'))
        db.session.commit()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def _user(self, uname, role, utype='doctor'):
        u = User(username=uname, email=f'{uname}@t.com', full_name=uname.title(), user_type=utype)
        u.set_password('123456'); u.roles.append(Role.query.filter_by(name=role).first())
        db.session.add(u); db.session.commit()
        return u

    def _patient(self, key):
        u = self._user(key, 'Patient', 'patient')
        p = Patient(user_id=u.id, date_of_birth=date(1970, 1, 1), gender='Male'); db.session.add(p); db.session.flush()
        return p

    def _login(self, u):
        self.client.post('/auth/login', data={'email': u.email, 'password': '123456'})

    def _run(self, patient, is_mel, prob):
        self._login(self.derm_user)
        with mock.patch('app.services.ai.skin_lesion_classification.classify_skin_lesion',
                        return_value=_skin_result(is_mel, prob)), \
             mock.patch('app.services.ai.skin_lesion_classification.skin_model_available', return_value=True):
            r = self.client.post('/ai/skin-lesion-detection', data={'file': (io.BytesIO(b'jpg'), 'lesion.jpg'),
                                                                     'patient_id': patient.id},
                                 content_type='multipart/form-data')
        return r


class MelanomaAlertTests(Base):
    def _doc(self, patient):
        d = PatientDocument(patient_id=patient.id, title='Dermoscopy', file_url='medical_documents/none.jpg',
                            document_type='clinical_image'); db.session.add(d); db.session.commit()
        return d

    def test_confident_melanoma_is_critical_assigned_with_urgent_task(self):
        from app.routes.ai import _alert_on_positive_finding
        doc = self._doc(self.p1)
        self._login(self.derm_user)
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(self.derm_user)
            _alert_on_positive_finding(_skin_result(True, 0.98), self.p1, doc)
        a = ClinicalAlert.query.filter_by(patient_id=self.p1.id, alert_type='AI_MELANOMA_SUSPECTED').one()
        self.assertEqual(a.severity, 'CRITICAL')
        self.assertEqual(a.assigned_to, self.derm_user.id)
        self.assertTrue(a.ai_assisted); self.assertAlmostEqual(a.confidence, 0.98)
        self.assertIn('biopsy', a.message)
        t = Task.query.filter_by(patient_id=self.p1.id, related_resource_type='clinical_alert').one()
        self.assertEqual((t.priority, t.assigned_to), ('URGENT', self.derm_user.id))
        # dermatologist dashboard shows the critical tile
        r = self.client.get('/doctor/dashboard')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'tile-danger', r.data)

    def test_borderline_melanoma_is_high_without_task(self):
        from app.routes.ai import _alert_on_positive_finding
        doc = self._doc(self.p1)
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(self.derm_user)
            _alert_on_positive_finding(_skin_result(True, 0.62), self.p1, doc)
        a = ClinicalAlert.query.filter_by(patient_id=self.p1.id).one()
        self.assertEqual(a.severity, 'HIGH')
        self.assertEqual(Task.query.filter_by(patient_id=self.p1.id).count(), 0)

    def test_nevus_raises_nothing(self):
        from app.routes.ai import _alert_on_positive_finding
        doc = self._doc(self.p2)
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(self.derm_user)
            _alert_on_positive_finding(_skin_result(False, 0.01), self.p2, doc)
        self.assertEqual(ClinicalAlert.query.filter_by(patient_id=self.p2.id).count(), 0)
        self.assertEqual(Task.query.filter_by(patient_id=self.p2.id).count(), 0)


class CareSettingSplitTests(Base):
    def _admit(self, patient):
        w = Ward(name='W'); db.session.add(w); db.session.flush()
        b = Bed(ward_id=w.id, bed_no='B1', status='Occupied'); db.session.add(b); db.session.flush()
        db.session.add(Admission(patient_id=patient.id, ward_id=w.id, bed_id=b.id, status='Admitted')); db.session.commit()

    def test_admin_dashboard_counts(self):
        self._admit(self.p1)
        self._login(self._user('adm', 'Admin', 'admin'))
        r = self.client.get('/admin/dashboard')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Outpatients', r.data); self.assertIn(b'Inpatients', r.data)
        self.assertIn(b'setting=outpatient', r.data); self.assertIn(b'setting=inpatient', r.data)

    def test_patient_list_filters_by_setting(self):
        self._admit(self.p1)
        self._login(self.derm_user)
        r = self.client.get('/doctor/patients?setting=inpatient')
        self.assertIn(b'>Ak<', r.data) if b'>Ak<' in r.data else self.assertIn(b'Ak', r.data)
        self.assertNotIn(b'ws@t.com', r.data)
        r = self.client.get('/doctor/patients?setting=outpatient')
        self.assertIn(b'ws@t.com', r.data)
        self.assertNotIn(b'ak@t.com', r.data)
        r = self.client.get('/doctor/patients')
        self.assertIn(b'ak@t.com', r.data); self.assertIn(b'ws@t.com', r.data)
        self.assertIn(b'seg-tab', r.data)


if __name__ == '__main__':
    unittest.main()
