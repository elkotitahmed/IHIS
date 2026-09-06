"""Physician + Patient experience, end to end.

Booking → physician opens patient → AI pre-visit summary → encounter with
smart diagnosis entry + autocomplete tags → differential support → lab and
radiology orders → prescription (rule-based safety shown) → results →
physician result review → follow-up → patient portal shows the right
information (and nothing clinician-only). Gemini is mocked throughout.
"""
import json
import unittest
from datetime import timedelta
from unittest import mock

from app import create_app, db
from app.models import (Appointment, Diagnosis, Doctor, ImagingType, LabOrder, LabResult,
                        LabTestCatalog, MedicalRecord, Medication, Patient, Prescription,
                        RadiologyOrder, Role, Specialty, Task, User)
from app.permissions import seed_permissions
from app.services.ai import platform
from app.utils import utcnow
from seed import ROLES


def _resp(text='AI text'):
    r = mock.Mock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.return_value = {'candidates': [{'content': {'parts': [{'text': text}]}}]}
    return r


class ExperienceBase(unittest.TestCase):
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
        self.spec = Specialty(name='General')
        db.session.add(self.spec)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        self.env.stop()
        platform.reset_runtime_state()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def user(self, name, utype, role):
        u = User(username=name, email=f'{name}@t.com', full_name=f'Test {name}', user_type=utype)
        u.set_password('123456')
        u.roles.append(self.roles[role])
        db.session.add(u)
        db.session.flush()
        if role == 'Doctor':
            db.session.add(Doctor(user_id=u.id, specialty_id=self.spec.id, consultation_fee=100.0))
        db.session.commit()
        return u

    def patient(self, name='pat'):
        u = User(username=name, email=f'{name}@t.com', full_name=f'Patient {name}', user_type='patient')
        u.set_password('123456')
        u.roles.append(self.roles['Patient'])
        db.session.add(u)
        db.session.flush()
        p = Patient(user_id=u.id, gender='M')
        db.session.add(p)
        db.session.commit()
        return p

    def login(self, email):
        self.client.get('/auth/logout')
        return self.client.post('/auth/login', data={'email': email, 'password': '123456'})

    def post_json(self, url, data):
        return self.client.post(url, data=json.dumps(data), content_type='application/json')


class PhysicianDashboardTests(ExperienceBase):
    def test_dashboard_is_minimal_with_one_ai_entry_point(self):
        doc = self.user('doc', 'doctor', 'Doctor')
        self.login(doc.email)
        r = self.client.get('/doctor/dashboard')
        self.assertEqual(r.status_code, 200)
        html = r.data.decode()
        for tile in ('Patients today', 'Waiting', 'Critical alerts', 'Pending results', 'Tasks', 'Follow-ups'):
            self.assertIn(tile, html)
        for section in ('TODAY', 'PATIENTS', 'WORK', 'RESULTS', 'SAFETY'):
            self.assertIn(section, html)
        # only TODAY is open by default; the rest are collapsed sections
        self.assertGreaterEqual(html.count('<details class="section-details"'), 5)
        self.assertEqual(html.count('<details class="section-details" open>'), 1)
        # exactly one AI entry point in the header, no scattered AI cards
        self.assertEqual(html.count('id="copilotBtn"'), 1)
        self.assertNotIn('AI chatbot', html)
        self.assertIn('Physician Dashboard', html)
        self.assertNotIn('Doctor Dashboard', html)
        # no Gemini call on dashboard load
        with mock.patch('requests.post') as post:
            self.client.get('/doctor/dashboard')
            post.assert_not_called()

    def test_inbox_has_tabs_and_ai_priority_is_deterministic(self):
        doc = self.user('doc', 'doctor', 'Doctor')
        p = self.patient()
        db.session.add(Diagnosis(patient_id=p.id, doctor_id=doc.doctor_profile.id, description='HTN'))
        from app.services import alerts as alert_svc
        a = alert_svc.create_alert(p.id, 'CRITICAL_RADIOLOGY', 'Pneumothorax on CXR', severity='CRITICAL')
        db.session.add(Task(title='Routine paperwork', task_type='GENERAL', assigned_to=doc.id, status='NEW',
                            priority='Normal', patient_id=p.id))
        db.session.commit()
        self.login(doc.email)
        r = self.client.get('/clinical/inbox')
        self.assertEqual(r.status_code, 200)
        for tab in ('Critical', 'Results', 'Referrals', 'Pharmacy Interventions', 'Tasks', 'Messages'):
            self.assertIn(tab.encode(), r.data)
        self.assertIn(b'What needs my attention first?', r.data)
        with mock.patch('requests.post', return_value=_resp('Start with the pneumothorax alert.')):
            d = self.post_json('/ai/copilot/priority', {}).get_json()
        self.assertEqual(d['items'][0]['kind'], 'critical_alert')      # AI cannot reorder this
        self.assertEqual(d['items'][-1]['kind'], 'task')
        self.assertEqual(d['ai']['status'], 'ok')

    def test_forms_carry_autocomplete_and_smart_diagnosis(self):
        doc = self.user('doc', 'doctor', 'Doctor')
        p = self.patient()
        db.session.add(Appointment(patient_id=p.id, doctor_id=doc.doctor_profile.id,
                                   scheduled_at=utcnow(), status='CheckedIn'))
        db.session.commit()
        self.login(doc.email)
        r = self.client.get(f'/doctor/patients/{p.id}/emr/add')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'data-dx-lookup', r.data)
        self.assertIn(b'data-ac="hpi"', r.data)
        self.assertIn(b'data-ac="plan"', r.data)
        self.assertIn(b'data-copilot-action="doc.hpi"', r.data)
        r = self.client.get(f'/doctor/patients/{p.id}/prescriptions')
        self.assertIn(b'id="rxSafety"', r.data)
        r = self.client.get('/doctor/appointments')
        self.assertIn(b'data-copilot-action="patient.previsit"', r.data)


class EndToEndJourneyTests(ExperienceBase):
    def test_patient_to_physician_to_patient_journey(self):
        doc = self.user('doc', 'doctor', 'Doctor')
        lab = self.user('lab', 'lab_technician', 'LabTechnician')
        p = self.patient()
        t = LabTestCatalog(test_name='HbA1c', category='Chem', normal_range='4-5.6', unit='%', price=20)
        it = ImagingType(name='Chest X-ray', price=50)
        med = Medication(generic_name='Metformin')
        db.session.add_all([t, it, med])
        db.session.commit()

        # 1. Patient books an appointment
        self.login('pat@t.com')
        when = (utcnow() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        r = self.client.post('/patient/appointments/book', data={
            'doctor_id': doc.doctor_profile.id, 'date': when.strftime('%Y-%m-%d'), 'time': when.strftime('%H:%M'),
            'reason': 'Diabetes review', 'duration_minutes': 30}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        appt = Appointment.query.filter_by(patient_id=p.id).first()
        self.assertIsNotNone(appt)

        # 2. Physician opens the patient and generates the pre-visit summary
        self.login(doc.email)
        self.assertEqual(self.client.get(f'/doctor/patients/{p.id}').status_code, 200)
        with mock.patch('requests.post', return_value=_resp('First visit; diabetes review requested.')):
            d = self.post_json('/ai/copilot/run', {'action': 'patient.previsit', 'patient_id': p.id}).get_json()
        self.assertEqual(d['ai']['status'], 'ok')
        self.assertIn('Diabetes review', ' '.join(i for s in d['verified'] for i in s['items']))

        # 3. Encounter: smart diagnosis lookup (local first), AI suggestion optional, physician selects
        d = self.client.get('/ai/copilot/diagnosis-lookup?q=type 2').get_json()
        self.assertTrue(any(x['code'] == 'E11.9' for x in d['terminology']))
        d = self.client.get('/ai/copilot/autocomplete?field=plan&q=order').get_json()
        self.assertTrue(any(s.startswith('Order') for s in d['suggestions']))
        r = self.client.post(f'/doctor/patients/{p.id}/emr/add', data={
            'diagnosis': 'Type 2 diabetes mellitus without complications', 'icd10': 'E11.9',
            'treatment_plan': 'Start metformin. Order HbA1c and chest X-ray.',
            'clinical_notes': 'Polyuria for 3 months.'}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(MedicalRecord.query.filter_by(patient_id=p.id).count(), 1)

        # 4. Differential support (explicit; never creates a diagnosis)
        n_dx = Diagnosis.query.count()
        with mock.patch('requests.post', return_value=_resp('{"considerations":[{"name":"T2DM"}],"red_flags":[],"questions":[],"investigations":[]}')):
            d = self.post_json('/ai/copilot/run', {'action': 'reasoning.differential', 'patient_id': p.id,
                                                    'inputs': {'complaint': 'polyuria'}}).get_json()
        self.assertEqual(d['ai']['status'], 'ok')
        self.assertEqual(Diagnosis.query.count(), n_dx)

        # 5. Orders and prescription (rule-based safety available immediately)
        from app.services.clinical_orders import create_lab_order, create_radiology_order, create_prescription
        d = self.client.get(f'/ai/copilot/medication-safety?patient_id={p.id}&medication_id={med.id}').get_json()
        self.assertEqual(d['level'], 'OK')
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(doc)
            lab_order = create_lab_order(p, doc.doctor_profile, t.id)
            create_radiology_order(p, doc.doctor_profile, it.id)
            create_prescription(p, doc.doctor_profile, [{'medication_id': med.id, 'dosage': '500 mg', 'frequency': 'bd'}])
            db.session.commit()
        lab_order = db.session.get(LabOrder, lab_order.id)
        self.assertEqual(Prescription.query.filter_by(patient_id=p.id).count(), 1)
        self.assertEqual(RadiologyOrder.query.filter_by(patient_id=p.id).count(), 1)

        # 6. Lab result verified → physician result review with AI
        lab_order.status = 'Finalized'
        db.session.add(LabResult(order_id=lab_order.id, result_value='8.2', result_unit='%', status='Verified',
                                 is_abnormal=True))
        db.session.commit()
        with mock.patch('requests.post', return_value=_resp('HbA1c 8.2% indicates suboptimal control.')):
            d = self.post_json('/ai/copilot/result-review', {'kind': 'lab', 'id': lab_order.id}).get_json()
        self.assertIn('8.2', ' '.join(i for s in d['verified'] for i in s['items']))
        self.assertEqual(d['ai']['status'], 'ok')

        # 7. Follow-up drafted, then the patient sees appropriate information only
        with mock.patch('requests.post', return_value=_resp('Review in 3 months with repeat HbA1c.')):
            d = self.post_json('/ai/copilot/run', {'action': 'doc.followup', 'patient_id': p.id}).get_json()
        self.assertEqual(d['ai']['status'], 'ok')
        self.login('pat@t.com')
        r = self.client.get('/patient/health-summary')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Metformin', r.data)
        self.assertIn(b'HbA1c', r.data)                     # released (Verified) result
        self.assertNotIn(b'Polyuria for 3 months', r.data)  # clinician note is not exposed
        r = self.client.get('/patient/dashboard')
        self.assertIn(b'MY RESULTS', r.data)
        with mock.patch('requests.post', return_value=_resp('HbA1c shows your average sugar.')):
            d = self.post_json('/ai/patient/explain-result', {'order_id': lab_order.id}).get_json()
        self.assertEqual(d['ai']['status'], 'ok')
        self.assertIn('What to discuss with your physician', [s['title'] for s in d['sections']])


if __name__ == '__main__':
    unittest.main()
