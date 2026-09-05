"""In-patient dashboard widget (#16): ward census + needs-attention board."""
import re
import unittest
from datetime import timedelta

from app import create_app, db
from app.models import (User, Role, Patient, Ward, Bed, Admission, ClinicalAlert)
from app.utils import utcnow

ROLES = ['SuperAdmin', 'Admin', 'Doctor', 'Nurse', 'Patient',
         'LabTechnician', 'Radiologist', 'Pharmacist', 'Receptionist',
         'Dentist', 'Physiotherapist']


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class InPatientBoardTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for name in ROLES:
            db.session.add(Role(name=name))
        db.session.commit()
        from app.permissions import seed_permissions
        seed_permissions(db)

        self.admin = self._make_user('admin@t.com', 'admin', 'Admin')
        self.nurse = self._make_user('nurse@t.com', 'nurse', 'Nurse')

        ward_a = Ward(name='Ward A', ward_type='General', room_charge_per_day=100.0)
        icu = Ward(name='ICU', ward_type='ICU', room_charge_per_day=500.0)
        db.session.add_all([ward_a, icu])
        db.session.flush()
        self.b1 = Bed(ward_id=ward_a.id, bed_no='B1', status='Occupied')
        self.b2 = Bed(ward_id=ward_a.id, bed_no='B2', status='Occupied')
        self.b3 = Bed(ward_id=icu.id, bed_no='I1', status='Available')
        db.session.add_all([self.b1, self.b2, self.b3])

        self.p_over = self._make_patient('over@t.com')
        self.p_alert = self._make_patient('alert@t.com')
        self.p_disc = self._make_patient('disc@t.com')

        now = utcnow()
        db.session.add(Admission(
            patient_id=self.p_over.id, ward_id=ward_a.id, bed_id=self.b1.id,
            admission_no='ADM-1001', status='Admitted',
            admitted_at=now - timedelta(days=7),
            expected_discharge=now - timedelta(days=2),
            reason='Pneumonia'))
        db.session.add(Admission(
            patient_id=self.p_alert.id, ward_id=ward_a.id, bed_id=self.b2.id,
            admission_no='ADM-1002', status='Admitted',
            admitted_at=now - timedelta(days=1),
            expected_discharge=now + timedelta(days=3),
            reason='Monitoring'))
        db.session.add(Admission(
            patient_id=self.p_disc.id, ward_id=ward_a.id, bed_id=self.b1.id,
            admission_no='ADM-1003', status='Discharged',
            admitted_at=now - timedelta(days=9),
            discharged_at=now - timedelta(days=1)))
        db.session.add(ClinicalAlert(
            patient_id=self.p_alert.id, alert_type='CRITICAL_LAB',
            severity='CRITICAL', status='OPEN',
            title='Critical lab value',
            message='Critical potassium result needs review.'))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _make_user(self, email, utype, role, password='123456'):
        u = User(username=email.split('@')[0], email=email, full_name='Test ' + utype,
                 user_type=utype)
        u.set_password(password)
        u.roles.append(Role.query.filter_by(name=role).first())
        db.session.add(u)
        db.session.commit()
        return u

    def _make_patient(self, email):
        u = User(username=email.split('@')[0], email=email, full_name='Pt ' + utype(email),
                 user_type='patient')
        u.set_password('123456')
        u.roles.append(Role.query.filter_by(name='Patient').first())
        db.session.add(u)
        db.session.commit()
        p = Patient(user_id=u.id)
        db.session.add(p)
        db.session.commit()
        return p

    def _login(self, email, password='123456'):
        self.client.get('/auth/logout')
        r = self.client.get('/auth/login')
        return self.client.post('/auth/login', data={
            'email': email, 'password': password, 'csrf_token': _csrf(r.data)},
            follow_redirects=True)

    def test_ward_census_and_attention(self):
        self._login('admin@t.com')
        r = self.client.get('/admissions/dashboard')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Ward A', r.data)
        self.assertIn(b'ICU', r.data)
        self.assertIn(b'2/2', r.data)      # Ward A fully occupied
        self.assertIn(b'0/1', r.data)      # ICU empty
        self.assertIn(b'Pt over', r.data)  # overstay patient listed
        self.assertIn(b'Pt alert', r.data)
        self.assertNotIn(b'Pt disc', r.data)  # discharged patient excluded
        self.assertIn(b'flag-overstay', r.data)
        self.assertIn(b'flag-alert', r.data)

    def test_after_discharge_bed_frees_and_patient_leaves_board(self):
        self._login('admin@t.com')
        adm = Admission.query.filter_by(admission_no='ADM-1001').first()
        adm.status = 'Discharged'
        adm.discharged_at = utcnow()
        self.b1.status = 'Available'
        db.session.commit()
        r = self.client.get('/admissions/dashboard')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(b'Pt over', r.data)
        self.assertIn(b'1/2', r.data)  # Ward A now 1 occupied of 2

    def test_nurse_can_view_board(self):
        self._login('nurse@t.com')
        r = self.client.get('/admissions/dashboard')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Ward A', r.data)


def utype(email):
    return email.split('@')[0]


if __name__ == '__main__':
    unittest.main()