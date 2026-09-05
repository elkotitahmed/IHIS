"""Tests for reference-range (observation density) across lab result views."""
import re
import unittest

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, LabTestCatalog,
                        LabOrder, LabResult)
from app.utils import utcnow


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class LabRefRangeTestCase(unittest.TestCase):
    ROLES = ['SuperAdmin', 'Admin', 'Doctor', 'Nurse', 'Patient',
             'LabTechnician', 'Radiologist', 'Pharmacist', 'Receptionist',
             'Dentist', 'Physiotherapist']

    def setUp(self):
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for n in self.ROLES:
            db.session.add(Role(name=n))
        from app.permissions import seed_permissions
        seed_permissions(db)
        self.spec = Specialty(name='General')
        db.session.add(self.spec)
        self.test = LabTestCatalog(test_name='CBC', is_active=True,
                                   normal_range='4.5 - 11.0', unit='x10^9/L',
                                   critical_low=0.5, critical_high=50.0)
        db.session.add(self.test)
        db.session.flush()

        self.doc = self._make_user('doc@t.com', 'doctor', 'Doctor')
        self.doc_profile = Doctor.query.filter_by(user_id=self.doc.id).first()
        self.pat = self._make_patient('pat@t.com')

        order = LabOrder(patient_id=self.pat.id, doctor_id=self.doc_profile.id,
                         test_id=self.test.id, status='FINALIZED',
                         order_date=utcnow())
        db.session.add(order)
        db.session.flush()
        db.session.add(LabResult(order_id=order.id, result_value='2.0',
                                 result_unit='x10^9/L', is_abnormal=True,
                                 is_critical=True, status='Verified',
                                 result_date=utcnow()))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _make_user(self, email, utype, role, password='123456'):
        u = User(username=email.split('@')[0], email=email,
                 full_name='Test ' + utype, user_type=utype)
        u.set_password(password)
        u.roles.append(Role.query.filter_by(name=role).first())
        db.session.add(u)
        if utype == 'doctor':
            db.session.flush()
            db.session.add(Doctor(user_id=u.id, specialty_id=self.spec.id,
                                  license_number='L' + email[:6]))
        db.session.commit()
        return u

    def _make_patient(self, email):
        u = User(username=email.split('@')[0], email=email,
                 full_name='Pat User', user_type='patient')
        u.set_password('123456')
        u.roles.append(Role.query.filter_by(name='Patient').first())
        db.session.add(u)
        db.session.commit()
        p = Patient(user_id=u.id, mrn='MRN-' + email[:3])
        db.session.add(p)
        db.session.commit()
        return p

    def _login(self, email, password='123456'):
        self.client.get('/auth/logout')
        r = self.client.get('/auth/login')
        return self.client.post('/auth/login', data={
            'email': email, 'password': password,
            'csrf_token': _csrf(r.data)}, follow_redirects=True)

    def test_doctor_lab_results_renders_value_and_range(self):
        self._login('doc@t.com')
        r = self.client.get('/doctor/lab-results')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'CBC', r.data)
        self.assertIn(b'4.5 - 11.0', r.data)
        self.assertIn(b'2.0', r.data)
        self.assertIn(b'CRITICAL', r.data)

    def test_patient_lab_results_renders_value_and_range(self):
        self._login('pat@t.com')
        r = self.client.get('/patient/lab-results')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'CBC', r.data)
        self.assertIn(b'4.5 - 11.0', r.data)
        self.assertIn(b'2.0', r.data)

    def test_summary_inline_ref_range(self):
        self._login('doc@t.com')
        r = self.client.get(f'/clinical/summary/{self.pat.id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'CBC', r.data)
        self.assertIn(b'4.5 - 11.0', r.data)

    def test_worklist_inline_ref_range(self):
        self._login('lab@t.com')
        lab = User(username='lab', email='lab@t.com', full_name='Lab Tech',
                   user_type='lab_technician')
        lab.set_password('123456')
        lab.roles.append(Role.query.filter_by(name='LabTechnician').first())
        db.session.add(lab)
        db.session.commit()
        self._login('lab@t.com')
        r = self.client.get('/lab/orders')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'4.5 - 11.0', r.data)
        self.assertIn(b'2.0', r.data)


if __name__ == '__main__':
    unittest.main()