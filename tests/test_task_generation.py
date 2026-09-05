"""Business-flow task generation coverage.

Verifies that clinical orders routed through the web UI auto-create the correct
task in the shared task engine:
- a doctor's lab order creates a Laboratory/LabTechnician/LAB task;
- a referral to a specific doctor creates a Care Coordination/Doctor/REFERRAL
  task for the receiving doctor (regression for the referral task gap).
"""
import unittest
from datetime import datetime

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, Task,
                        LabTestCatalog, MedicalRecord)
from app.permissions import seed_permissions
from seed import ROLES


class TaskGenerationTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for name in ROLES:
            db.session.add(Role(name=name))
        db.session.add(Specialty(name='General'))
        db.session.commit()
        seed_permissions(db)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk(self, uname, utype, role, doctor=False):
        u = User(username=uname, email=f'{uname}@t.com',
                 full_name='Test ' + uname, user_type=utype)
        u.set_password('123456')
        u.roles.append(Role.query.filter_by(name=role).first())
        db.session.add(u)
        db.session.flush()
        if doctor:
            spec = Specialty.query.first()
            db.session.add(Doctor(user_id=u.id, specialty_id=spec.id))
            db.session.flush()
        return u

    def _login(self, client, email):
        client.get('/auth/logout')
        client.post('/auth/login', data={
            'email': email, 'password': '123456',
        }, follow_redirects=True)

    def test_lab_order_creates_lab_task(self):
        doc = self._mk('doc', 'doctor', 'Doctor', doctor=True)
        pat_u = self._mk('pat', 'patient', 'Patient')
        pat = Patient(user_id=pat_u.id)
        db.session.add(pat)
        db.session.flush()
        test = LabTestCatalog(test_name='CBC', price=10.0, is_active=True)
        db.session.add(test)
        # give the doctor a documented relationship via an appointment/mr
        db.session.add(MedicalRecord(patient_id=pat.id, doctor_id=doc.doctor_profile.id,
                                     visit_date=datetime.now().date()))
        db.session.commit()

        client = self.app.test_client()
        self._login(client, 'doc@t.com')
        r = client.post(f'/doctor/patients/{pat.id}/lab-order', data={
            'test_id': test.id, 'priority': 'High',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        task = Task.query.filter_by(task_type='LAB').first()
        self.assertIsNotNone(task, 'no LAB task created')
        self.assertEqual(task.department, 'Laboratory')
        self.assertEqual(task.assigned_role, 'LabTechnician')
        self.assertEqual(task.patient_id, pat.id)

    def test_referral_to_doctor_creates_referral_task(self):
        doc = self._mk('doc', 'doctor', 'Doctor', doctor=True)
        to_doc = self._mk('target', 'doctor', 'Doctor', doctor=True)
        pat_u = self._mk('pat', 'patient', 'Patient')
        pat = Patient(user_id=pat_u.id)
        db.session.add(pat)
        db.session.flush()
        db.session.add(MedicalRecord(patient_id=pat.id, doctor_id=doc.doctor_profile.id,
                                     visit_date=datetime.now().date()))
        db.session.commit()

        client = self.app.test_client()
        self._login(client, 'doc@t.com')
        r = client.post('/care/referrals/new', data={
            'patient_id': pat.id,
            'to_doctor_id': to_doc.doctor_profile.id,
            'reason': 'Cardiology evaluation needed',
            'urgency': 'Urgent',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        task = Task.query.filter_by(task_type='REFERRAL').first()
        self.assertIsNotNone(task, 'no REFERRAL task created')
        self.assertEqual(task.department, 'Care Coordination')
        self.assertEqual(task.assigned_to, to_doc.id)
        self.assertEqual(task.patient_id, pat.id)
        # the receiving doctor sees it in their task list
        self._login(client, 'target@t.com')
        page = client.get('/tasks/my-tasks')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Referral', page.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()