"""Head-CT haemorrhage workbench: page, mocked analysis, alert routing, media, policy."""
import io
import unittest
from unittest import mock

from app import create_app, db
from app.models import ClinicalAlert, Patient, PatientDocument, Role, Task, User
from app.permissions import seed_permissions
from seed import ROLES


def _slice_result(positive, prob):
    return {'feature': 'ich', 'mode': 'slice', 'orig_key': 'brain_x.png',
            'windows': {'brain': 'brain_x.png', 'subdural': 'subdural_x.png', 'bone': 'bone_x.png'},
            'heatmap_key': 'cam_convnext_x.png', 'heatmap_model': 'ConvNeXt-Tiny',
            'per_model': [{'model': 'ConvNeXt-Tiny', 'probability': prob, 'percent': round(prob * 100, 1), 'positive': positive, 'peak': (1, 1), 'cam_key': 'cam_convnext_x.png'},
                          {'model': 'Swin-Tiny', 'probability': prob, 'percent': round(prob * 100, 1), 'positive': positive, 'peak': (1, 1), 'cam_key': 'cam_swin_x.png'}],
            'probability': prob, 'percent': round(prob * 100, 1), 'positive': positive, 'agree': True,
            'threshold': 0.103, 'flat_image': False, 'note': '', 'meta': {}, 'models_used': ['ConvNeXt-Tiny', 'Swin-Tiny'],
            'sequence_model': False, 'n_slices': 1, 'seconds': 0.4}


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

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def _user(self, uname, role, utype):
        u = User(username=uname, email=f'{uname}@t.com', full_name=uname.title(), user_type=utype)
        u.set_password('123456'); u.roles.append(Role.query.filter_by(name=role).first())
        db.session.add(u); db.session.commit()
        return u

    def _login(self, u):
        self.client.post('/auth/login', data={'email': u.email, 'password': '123456'})

    def _patient_with_doc(self):
        pu = self._user('pt', 'Patient', 'patient')
        p = Patient(user_id=pu.id); db.session.add(p); db.session.flush()
        d = PatientDocument(patient_id=p.id, title='Head CT', file_url='medical_documents/none.dcm', document_type='clinical_image')
        db.session.add(d); db.session.commit()
        return p, d


class PageTests(Base):
    def test_page_and_mocked_slice_analysis(self):
        self._login(self._user('radio', 'Radiologist', 'radiologist'))
        r = self.client.get('/ai/ich-detection')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Head CT Haemorrhage Detection', r.data)
        self.assertIn(b'multiple', r.data)                                  # series upload allowed
        with mock.patch('app.services.ai.ich_detection.analyze_ich', return_value=_slice_result(True, 0.62)), \
             mock.patch('app.services.ai.ich_detection.ich_model_available', return_value=True):
            r = self.client.post('/ai/ich-detection', data={'file': (io.BytesIO(b'DICM'), 's00.dcm')},
                                 content_type='multipart/form-data')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Intracranial haemorrhage suspected', r.data)
        self.assertIn(b'LayerCAM', r.data)
        self.assertIn(b'62.0%', r.data)

    def test_denied_for_pharmacist_and_dermatologist(self):
        self._login(self._user('pharma', 'Pharmacist', 'pharmacist'))
        self.assertIn(self.client.get('/ai/ich-detection').status_code, (302, 403))
        from app.models import Doctor, Specialty
        du = self._user('derm', 'Doctor', 'doctor'); sp = Specialty(name='Dermatology'); db.session.add(sp); db.session.flush()
        db.session.add(Doctor(user_id=du.id, specialty_id=sp.id)); db.session.commit()
        self.client.get('/auth/logout')
        self._login(du)
        r = self.client.get('/ai/ich-detection')
        self.assertEqual(r.status_code, 302); self.assertIn('/ai/hub', r.headers['Location'])

    def test_neurologist_sees_only_ich(self):
        from app.models import Doctor, Specialty
        from app.services.ai.specialty_models import allowed_models
        du = self._user('neuro', 'Doctor', 'doctor'); sp = Specialty(name='Neurology'); db.session.add(sp); db.session.flush()
        db.session.add(Doctor(user_id=du.id, specialty_id=sp.id)); db.session.commit()
        self.assertEqual(allowed_models(du), {'ich'})


class AlertTests(Base):
    def test_confident_positive_is_critical_with_task(self):
        from app.routes.ai import _alert_on_positive_finding
        p, d = self._patient_with_doc()
        radio = self._user('radio', 'Radiologist', 'radiologist')
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(radio)
            _alert_on_positive_finding(_slice_result(True, 0.71), p, d)
        a = ClinicalAlert.query.filter_by(patient_id=p.id, alert_type='AI_ICH_SUSPECTED').one()
        self.assertEqual((a.severity, a.assigned_to, a.ai_assisted), ('CRITICAL', radio.id, True))
        self.assertIn('haemorrhage', a.title)
        self.assertEqual(Task.query.filter_by(related_resource_type='clinical_alert', related_resource_id=a.id).count(), 1)

    def test_low_confidence_positive_is_high_without_task(self):
        from app.routes.ai import _alert_on_positive_finding
        p, d = self._patient_with_doc()
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(self._user('radio', 'Radiologist', 'radiologist'))
            _alert_on_positive_finding(_slice_result(True, 0.15), p, d)
        a = ClinicalAlert.query.filter_by(patient_id=p.id).one()
        self.assertEqual(a.severity, 'HIGH'); self.assertEqual(Task.query.count(), 0)

    def test_negative_raises_nothing(self):
        from app.routes.ai import _alert_on_positive_finding
        p, d = self._patient_with_doc()
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(self._user('radio', 'Radiologist', 'radiologist'))
            _alert_on_positive_finding(_slice_result(False, 0.02), p, d)
        self.assertEqual(ClinicalAlert.query.count(), 0)


class MediaAndCatalogueTests(Base):
    def test_ich_results_served_to_staff_only(self):
        import os
        base = self.app.config.get('UPLOAD_FOLDER') or 'var/uploads'
        d = os.path.join(base, 'ai', 'ich', 'results'); os.makedirs(d, exist_ok=True)
        name = 'test_ich_media.png'
        with open(os.path.join(d, name), 'wb') as f:
            f.write(b'PNGSTUB' + b'0' * 32)
        try:
            self._login(self._user('radio', 'Radiologist', 'radiologist'))
            r = self.client.get(f'/ai/media/ich/results/{name}')
            self.assertEqual(r.status_code, 200); r.close()
        finally:
            os.remove(os.path.join(d, name))

    def test_hub_landing_and_capabilities_list_five_models(self):
        self._login(self._user('adm', 'Admin', 'admin'))
        with mock.patch('app.services.ai.ich_detection.ich_model_available', return_value=True):
            hub = self.client.get('/ai/hub').data.decode()
        self.assertIn('Head CT Haemorrhage Detection', hub)
        self.assertIn('/5', hub)
        self.assertIn('href="/ai/ich-detection"', self.client.get('/admin/dashboard').data.decode())
        self.assertIn('Head CT Haemorrhage', self.client.get('/home').data.decode())


if __name__ == '__main__':
    unittest.main()
