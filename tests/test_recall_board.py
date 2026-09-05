"""Tests for the Recall Board (scheduled follow-ups + reminders bucketed by due)."""
import re
import unittest
from datetime import timedelta

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, LabTestCatalog,
                        LabOrder, FollowUp, ClinicalReminder)
from app.utils import utcnow


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class RecallBoardTestCase(unittest.TestCase):
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
        self.test = LabTestCatalog(test_name='CBC', is_active=True)
        db.session.add(self.test)
        db.session.flush()

        self.doc = self._make_user('doc@t.com', 'doctor', 'Doctor')
        self.doc_profile = Doctor.query.filter_by(user_id=self.doc.id).first()
        self.pat = self._make_patient('pat@t.com')
        # documented need-to-know for the doctor via a lab order
        db.session.add(LabOrder(patient_id=self.pat.id,
                                doctor_id=self.doc_profile.id,
                                test_id=self.test.id, status='Pending'))
        db.session.flush()
        self.today = utcnow().date()
        db.session.add(FollowUp(patient_id=self.pat.id,
                                provider_id=self.doc_profile.id,
                                scheduled_for=utcnow() - timedelta(days=3),
                                reason='BP recheck', status='Scheduled'))
        db.session.add(FollowUp(patient_id=self.pat.id,
                                provider_id=self.doc_profile.id,
                                scheduled_for=utcnow() + timedelta(days=30),
                                reason='Next recall', status='Scheduled'))
        db.session.add(ClinicalReminder(patient_id=self.pat.id,
                                        reminder_type='IMMUNIZATION',
                                        title='Flu shot',
                                        due_date=self.today,
                                        priority='High', status='OPEN'))
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

    def test_board_buckets_overdue_today_upcoming(self):
        self._login('doc@t.com')
        r = self.client.get('/clinical/recall-board')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Recall Board', r.data)
        self.assertIn(b'BP recheck', r.data)
        self.assertIn(b'Next recall', r.data)
        self.assertIn(b'Flu shot', r.data)
        self.assertIn(b'Overdue', r.data)
        self.assertIn(b'Pat User', r.data)

    def test_reminder_done_from_board(self):
        self._login('doc@t.com')
        r = self.client.get('/clinical/recall-board')
        csrf = _csrf(r.data)
        rem = ClinicalReminder.query.filter_by(patient_id=self.pat.id).first()
        res = self.client.post(f'/clinical/reminders/{rem.id}/done',
                               data={'csrf_token': csrf},
                               follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(rem.status, 'DONE')
        r = self.client.get('/clinical/recall-board')
        self.assertNotIn(b'Flu shot', r.data)


if __name__ == '__main__':
    unittest.main()