"""Chest X-ray screening page, local dictation endpoint, drug reference page and
the no-show model — all with the heavy parts mocked (no model, no network)."""
import io
import unittest
from datetime import datetime, timedelta
from unittest import mock

from app import create_app, db
from app.models import Appointment, Doctor, Medication, Patient, Role, Specialty, User
from app.permissions import seed_permissions
from seed import ROLES


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

    def _user(self, uname, role, utype='doctor'):
        u = User(username=uname, email=f'{uname}@t.com', full_name=uname.title(), user_type=utype)
        u.set_password('123456'); u.roles.append(Role.query.filter_by(name=role).first())
        db.session.add(u); db.session.commit()
        return u

    def _login(self, u):
        self.client.post('/auth/login', data={'email': u.email, 'password': '123456'})


class ChestXrayTests(Base):
    def test_page_and_mocked_analysis(self):
        self._login(self._user('radio', 'Radiologist', 'radiologist'))
        r = self.client.get('/ai/chest-xray')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Chest X-ray Screening', r.data)
        fake = {'findings': [{'label': 'Pneumothorax', 'name': 'Pneumothorax (air around the lung)', 'probability': .91, 'percent': 91, 'flag': True}],
                'top': [{'label': 'Pneumothorax', 'name': 'Pneumothorax (air around the lung)', 'probability': .91, 'percent': 91, 'flag': True}],
                'critical': [{'label': 'Pneumothorax', 'name': 'Pneumothorax (air around the lung)', 'probability': .91, 'percent': 91, 'flag': True}],
                'orig_key': 'x.png', 'feature': 'chest', 'model': 'densenet121-res224-all', 'note': 'n'}
        with mock.patch('app.services.ai.chest_xray.analyze_chest_xray', return_value=dict(fake)):
            r = self.client.post('/ai/chest-xray', data={'file': (io.BytesIO(b'png'), 'x.png')}, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Possible critical finding', r.data)
        self.assertIn(b'91%', r.data)

    def test_denied_for_pharmacist(self):
        self._login(self._user('pharma', 'Pharmacist', 'pharmacist'))
        self.assertIn(self.client.get('/ai/chest-xray').status_code, (302, 403))


class DictationTests(Base):
    def test_transcribe_endpoint_uses_local_model(self):
        self._login(self._user('doc', 'Doctor'))
        with mock.patch('app.services.ai.dictation.dictation_available', return_value=True), \
             mock.patch('app.services.ai.dictation.transcribe', return_value={'text': 'chest pain for two days', 'language': 'en', 'duration': 3.2}):
            r = self.client.post('/ai/copilot/transcribe', data={'audio': (io.BytesIO(b'webm'), 'd.webm'), 'language': 'en'},
                                 content_type='multipart/form-data')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['text'], 'chest pain for two days')

    def test_transcribe_requires_audio(self):
        self._login(self._user('doc2', 'Doctor'))
        with mock.patch('app.services.ai.dictation.dictation_available', return_value=True):
            r = self.client.post('/ai/copilot/transcribe', data={}, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 400)

    def test_dictation_script_only_for_ai_staff(self):
        self._login(self._user('doc3', 'Doctor'))
        with mock.patch('app.services.ai.dictation.dictation_available', return_value=True):
            r = self.client.get('/ai/hub')
        self.assertIn(b'dictation.js', r.data)


class DrugReferenceTests(Base):
    def test_reference_page_with_mocked_apis(self):
        self._login(self._user('pharma2', 'Pharmacist', 'pharmacist'))
        med = Medication(generic_name='Warfarin', brand_name='Coumadin'); db.session.add(med); db.session.commit()
        label = {'brand': 'COUMADIN', 'manufacturer': 'X', 'effective_time': '20240101', 'source': 'openFDA drug label',
                 'sections': [('Boxed warning', 'Bleeding risk.'), ('Drug interactions', 'Aspirin increases bleeding.')]}
        with mock.patch('app.services.drug_reference.fda_label', return_value=label), \
             mock.patch('app.services.drug_reference.rxnorm', return_value={'rxcui': '11289', 'name': 'Warfarin'}):
            r = self.client.get(f'/pharmacy/medications/{med.id}/reference')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Bleeding risk', r.data)
        self.assertIn(b'11289', r.data)
        self.assertIn(b'DDInter', r.data)

    def test_offline_is_graceful(self):
        self._login(self._user('pharma3', 'Pharmacist', 'pharmacist'))
        med = Medication(generic_name='Xyzzy'); db.session.add(med); db.session.commit()
        with mock.patch('app.services.drug_reference.requests.get', side_effect=OSError('offline')):
            r = self.client.get(f'/pharmacy/medications/{med.id}/reference')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'No label available', r.data)


class NoShowTests(Base):
    def _seed_history(self, n):
        spec = Specialty(name='GP'); db.session.add(spec); db.session.flush()
        du = self._user('drx', 'Doctor'); d = Doctor(user_id=du.id, specialty_id=spec.id); db.session.add(d)
        pu = self._user('pat', 'Patient', 'patient'); p = Patient(user_id=pu.id); db.session.add(p); db.session.flush()
        base = datetime.utcnow() - timedelta(days=200)
        for i in range(n):
            db.session.add(Appointment(patient_id=p.id, doctor_id=d.id, scheduled_at=base + timedelta(days=i % 150, hours=i % 9),
                                       created_at=base + timedelta(days=(i % 150) - (i % 7)),
                                       status='NoShow' if i % 4 == 0 else 'Completed'))
        db.session.commit()
        return d, p

    def test_refuses_with_thin_history(self):
        from app.services import noshow
        self._seed_history(20)
        noshow.train(force=True)
        st = noshow.status()
        self.assertFalse(st['trained'])
        self.assertEqual(st['rows'], 20)

    def test_trains_and_predicts_with_enough_history(self):
        from app.services import noshow
        d, p = self._seed_history(200)
        noshow.train(force=True)
        self.assertTrue(noshow.status()['trained'])
        up = Appointment(patient_id=p.id, doctor_id=d.id, scheduled_at=datetime.utcnow() + timedelta(days=2), status='Scheduled')
        db.session.add(up); db.session.commit()
        probs = noshow.predict([up])
        self.assertIn(up.id, probs)
        self.assertTrue(0.0 <= probs[up.id] <= 1.0)

    def test_capacity_page_shows_model_state(self):
        from app.services import noshow
        self._seed_history(10)
        noshow.train(force=True)
        self._login(self._user('adm', 'Admin', 'admin'))
        r = self.client.get('/admin/capacity')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'No prediction yet', r.data)


class ChestMediaTests(Base):
    def test_uploaded_chest_image_is_served_to_staff(self):
        import os
        base = self.app.config.get('UPLOAD_FOLDER') or 'var/uploads'
        d = os.path.join(base, 'ai', 'chest', 'uploads'); os.makedirs(d, exist_ok=True)
        name = 'test_chest_media.png'
        with open(os.path.join(d, name), 'wb') as f:
            f.write(b'PNGSTUB' + b'0' * 32)
        try:
            self._login(self._user('radio2', 'Radiologist', 'radiologist'))
            r = self.client.get(f'/ai/media/chest/uploads/{name}')
            self.assertEqual(r.status_code, 200)
            r.close()                      # send_file keeps the handle open until the response is closed
            self.assertEqual(self.client.get('/ai/media/chest/uploads/missing.png').status_code, 404)
        finally:
            os.remove(os.path.join(d, name))


if __name__ == '__main__':
    unittest.main()
