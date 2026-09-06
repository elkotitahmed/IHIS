"""Patient-facing AI: safe, own-record only, static-first, never diagnoses.

Includes the mandatory cross-patient isolation test (Patient A -> Patient B
data MUST FAIL) and the no-cross-patient-cache guarantee.
"""
import json
import unittest
from unittest import mock

from app import create_app, db
from app.models import (AIUsageLog, Appointment, Doctor, LabOrder, LabResult, LabTestCatalog,
                        Medication, Patient, Prescription, PrescriptionItem, Problem, Role,
                        Specialty, User)
from app.permissions import seed_permissions
from app.services.ai import platform
from seed import ROLES


def _resp(text='AI text'):
    r = mock.Mock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.return_value = {'candidates': [{'content': {'parts': [{'text': text}]}}]}
    return r


class PatientAiBase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.roles = {}
        for name in ROLES:
            r = Role(name=name)
            db.session.add(r)
            self.roles[name] = r
        db.session.commit()
        seed_permissions(db)
        platform.reset_runtime_state()
        self.env = mock.patch.dict('os.environ', {'GEMINI_API_KEY': 'test-key-not-real'})
        self.env.start()
        spec = Specialty(name='General')
        db.session.add(spec)
        db.session.flush()
        du = User(username='doc', email='doc@t.com', full_name='Test doc', user_type='doctor')
        du.set_password('123456')
        du.roles.append(self.roles['Doctor'])
        db.session.add(du)
        db.session.flush()
        self.doctor = Doctor(user_id=du.id, specialty_id=spec.id, consultation_fee=1)
        db.session.add(self.doctor)
        db.session.commit()
        self.a = self._patient('alice')
        self.b = self._patient('bob')
        t = LabTestCatalog(test_name='Hemoglobin', category='Hem', normal_range='12-16', unit='g/dL', price=5)
        db.session.add(t)
        db.session.flush()
        self.orders = {}
        for p in (self.a, self.b):
            o = LabOrder(patient_id=p.id, doctor_id=self.doctor.id, test_id=t.id, status='Finalized')
            db.session.add(o)
            db.session.flush()
            db.session.add(LabResult(order_id=o.id, result_value='9.5', result_unit='g/dL', status='Finalized',
                                     is_abnormal=True))
            self.orders[p.id] = o
        med = Medication(generic_name='Metformin')
        db.session.add(med)
        db.session.flush()
        self.items = {}
        for p in (self.a, self.b):
            rx = Prescription(patient_id=p.id, doctor_id=self.doctor.id, status='Active')
            db.session.add(rx)
            db.session.flush()
            it = PrescriptionItem(prescription_id=rx.id, medication_id=med.id, dosage='500 mg', frequency='twice daily')
            db.session.add(it)
            db.session.flush()
            self.items[p.id] = it
        from datetime import timedelta
        from app.utils import utcnow
        self.appt = Appointment(patient_id=self.a.id, doctor_id=self.doctor.id,
                                scheduled_at=utcnow() + timedelta(days=2), status='Scheduled',
                                reason='Diabetes review', visit_type='Follow-up')
        db.session.add(self.appt)
        db.session.add(Problem(patient_id=self.a.id, description='Type 2 diabetes', status='Active'))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        self.env.stop()
        platform.reset_runtime_state()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _patient(self, name):
        u = User(username=name, email=f'{name}@t.com', full_name=f'Patient {name}', user_type='patient')
        u.set_password('123456')
        u.roles.append(self.roles['Patient'])
        db.session.add(u)
        db.session.flush()
        p = Patient(user_id=u.id)
        db.session.add(p)
        db.session.commit()
        return p

    def login(self, email):
        self.client.get('/auth/logout')
        return self.client.post('/auth/login', data={'email': email, 'password': '123456'})

    def post_json(self, url, data):
        return self.client.post(url, data=json.dumps(data), content_type='application/json')


class PatientAiTests(PatientAiBase):
    def test_explain_own_result_static_first_then_ai(self):
        self.login('alice@t.com')
        with mock.patch('requests.post', return_value=_resp('Your haemoglobin is a little low.')) as post:
            d = self.post_json('/ai/patient/explain-result', {'order_id': self.orders[self.a.id].id}).get_json()
            sent = post.call_args.kwargs['json']['contents'][0]['parts'][0]['text']
        self.assertTrue(d['ok'])
        titles = [s['title'] for s in d['sections']]
        self.assertIn('What the test is', titles)
        self.assertIn('What to discuss with your physician', titles)
        self.assertEqual(d['ai']['status'], 'ok')
        self.assertIn('never tell them to start, stop or change', sent)
        self.assertNotIn('Patient alice', sent)
        self.assertIn('not a diagnosis', d['footer'])

    def test_patient_a_cannot_explain_patient_b_data(self):
        self.login('alice@t.com')
        with mock.patch('requests.post') as post:
            r = self.post_json('/ai/patient/explain-result', {'order_id': self.orders[self.b.id].id})
            self.assertEqual(r.status_code, 404)
            r = self.post_json('/ai/patient/explain-medication', {'item_id': self.items[self.b.id].id})
            self.assertEqual(r.status_code, 404)
            post.assert_not_called()

    def test_no_cross_patient_cache(self):
        self.login('alice@t.com')
        with mock.patch('requests.post', return_value=_resp('Alice text')) as post:
            self.post_json('/ai/patient/explain-medication', {'item_id': self.items[self.a.id].id})
            self.assertEqual(post.call_count, 1)
        self.login('bob@t.com')
        with mock.patch('requests.post', return_value=_resp('Bob text')) as post:
            d = self.post_json('/ai/patient/explain-medication', {'item_id': self.items[self.b.id].id}).get_json()
            self.assertEqual(post.call_count, 1)          # Bob never receives Alice's cached output
        self.assertEqual(d['ai']['text'], 'Bob text')

    def test_unreleased_result_is_not_explained(self):
        o = self.orders[self.a.id]
        o.result.status = 'Draft'
        db.session.commit()
        self.login('alice@t.com')
        with mock.patch('requests.post') as post:
            d = self.post_json('/ai/patient/explain-result', {'order_id': o.id}).get_json()
            post.assert_not_called()
        self.assertEqual(d['sections'][0]['title'], 'Not ready yet')

    def test_appointment_preparation_is_static_and_free(self):
        self.login('alice@t.com')
        with mock.patch('requests.post') as post:
            d = self.post_json('/ai/patient/prepare-appointment', {'appointment_id': self.appt.id}).get_json()
            post.assert_not_called()
        self.assertTrue(d['ok'])
        flat = ' '.join(i for s in d['sections'] for i in s['items'])
        self.assertIn('home readings', flat)          # follow-up checklist
        self.assertIn('Diabetes review', flat)
        self.assertIsNone(d['ai'])

    def test_term_explainer_local_then_ai(self):
        self.login('alice@t.com')
        with mock.patch('requests.post') as post:
            d = self.post_json('/ai/patient/explain-term', {'term': 'hypertension'}).get_json()
            post.assert_not_called()
        self.assertIn('High blood pressure', d['explanation'])
        with mock.patch('requests.post', return_value=_resp('A plain explanation.')):
            d = self.post_json('/ai/patient/explain-term', {'term': 'cholecystectomy'}).get_json()
        self.assertIsNone(d['explanation'])
        self.assertEqual(d['ai']['status'], 'ok')

    def test_summary_uses_only_visible_data(self):
        self.login('alice@t.com')
        with mock.patch('requests.post', return_value=_resp('Summary')) as post:
            d = self.post_json('/ai/patient/summary', {}).get_json()
            sent = post.call_args.kwargs['json']['contents'][0]['parts'][0]['text']
        self.assertIn('Type 2 diabetes', sent)
        self.assertIn('Terms explained', [s['title'] for s in d['sections']])

    def test_physician_ai_endpoints_closed_to_patients_and_vice_versa(self):
        self.login('alice@t.com')
        self.assertEqual(self.client.get('/ai/copilot/panel').status_code, 403)
        self.assertEqual(self.client.get('/ai/copilot/radiology').status_code, 403)
        self.login('doc@t.com')
        r = self.post_json('/ai/patient/explain-term', {'term': 'hypertension'})
        self.assertEqual(r.status_code, 403)

    def test_ai_failure_keeps_static_content(self):
        import requests
        self.login('alice@t.com')
        with mock.patch('requests.post', side_effect=requests.Timeout()):
            d = self.post_json('/ai/patient/explain-medication', {'item_id': self.items[self.a.id].id}).get_json()
        self.assertEqual(d['ai']['status'], 'error')
        self.assertIn('Purpose', [s['title'] for s in d['sections']])
        self.assertTrue(AIUsageLog.query.filter_by(status='error').count() >= 1)

    def test_portal_pages_render_with_ai_buttons(self):
        self.login('alice@t.com')
        for url in ('/patient/dashboard', '/patient/health-summary', '/patient/lab-results',
                    '/patient/prescriptions', '/patient/appointments'):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertIn(b'data-pai=', r.data, url)
        r = self.client.get('/patient/dashboard')
        for label in (b'MY HEALTH', b'MY APPOINTMENTS', b'MY MEDICATIONS', b'MY RESULTS', b'MY DOCUMENTS',
                      b'MY FOLLOW-UP', b'MY MESSAGES', b'MY NOTIFICATIONS'):
            self.assertIn(label, r.data)
        self.assertNotIn(b'id="copilotBtn"', r.data)
        self.assertIn(b'patient_ai.js', r.data)


if __name__ == '__main__':
    unittest.main()
