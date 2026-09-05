"""Tests for the clinical reminder engine (recall) and its route."""
import re
import unittest
from datetime import date, datetime, timedelta

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, CareTeam,
                        CareTeamMember, FollowUp, ImmunizationRecord,
                        ClinicalReminder, utcnow)


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class ReminderTestCase(unittest.TestCase):
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
        db.session.flush()
        self.doc = self._make_user('doc@t.com', 'doctor', 'Doctor')
        self.pat = self._make_patient('pat@t.com')
        team = CareTeam(patient_id=self.pat.id, name='Team')
        db.session.add(team)
        db.session.flush()
        db.session.add(CareTeamMember(team_id=team.id, user_id=self.doc.id,
                                      role='Physician'))
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

    def _login(self, email='doc@t.com'):
        self.client.get('/auth/logout')
        r = self.client.get('/auth/login')
        return self.client.post('/auth/login', data={
            'email': email, 'password': '123456',
            'csrf_token': _csrf(r.data)}, follow_redirects=True)

    def test_followup_materialises_reminder(self):
        from app.services.reminders import scan_due_reminders
        doc_profile = Doctor.query.filter_by(user_id=self.doc.id).first()
        db.session.add(FollowUp(patient_id=self.pat.id, provider_id=doc_profile.id,
                                scheduled_for=utcnow() - timedelta(days=2),
                                reason='Wound check', status='Scheduled'))
        db.session.commit()
        self._login()
        r = self.client.get('/clinical/reminders')
        self.assertEqual(r.status_code, 200)
        reminder = ClinicalReminder.query.filter_by(
            patient_id=self.pat.id, reminder_type='FOLLOWUP').first()
        self.assertIsNotNone(reminder)
        self.assertEqual(reminder.status, 'OPEN')

    def test_immunization_next_due_reminder(self):
        db.session.add(ImmunizationRecord(patient_id=self.pat.id,
                                          vaccine_name='HepB',
                                          administered_at=utcnow(),
                                          next_due=date.today()))
        db.session.commit()
        self._login()
        self.client.get('/clinical/reminders')
        rem = ClinicalReminder.query.filter_by(
            patient_id=self.pat.id, reminder_type='IMMUNIZATION').first()
        self.assertIsNotNone(rem)
        self.assertEqual(rem.source_type, 'immunization')

    def test_dedupe_and_done(self):
        from app.services.reminders import scan_due_reminders
        doc_profile = Doctor.query.filter_by(user_id=self.doc.id).first()
        fu = FollowUp(patient_id=self.pat.id, provider_id=doc_profile.id,
                      scheduled_for=utcnow() - timedelta(days=1),
                      reason='Review', status='Scheduled')
        db.session.add(fu)
        db.session.commit()
        scan_due_reminders(actor_id=self.doc.id)
        scan_due_reminders(actor_id=self.doc.id)
        count = ClinicalReminder.query.filter_by(
            patient_id=self.pat.id, reminder_type='FOLLOWUP').count()
        self.assertEqual(count, 1)
        rem = ClinicalReminder.query.filter_by(
            patient_id=self.pat.id, reminder_type='FOLLOWUP').first()
        self._login()
        page = self.client.get('/clinical/reminders')
        self.client.post(f'/clinical/reminders/{rem.id}/done',
                         data={'csrf_token': _csrf(page.data)},
                         follow_redirects=True)
        db.session.refresh(rem)
        self.assertEqual(rem.status, 'DONE')
        self.assertEqual(rem.completed_by, self.doc.id)

    def test_auto_close_when_source_resolved(self):
        from app.services.reminders import scan_due_reminders
        doc_profile = Doctor.query.filter_by(user_id=self.doc.id).first()
        fu = FollowUp(patient_id=self.pat.id, provider_id=doc_profile.id,
                      scheduled_for=utcnow() - timedelta(days=1),
                      reason='Follow', status='Scheduled')
        db.session.add(fu)
        db.session.commit()
        scan_due_reminders(actor_id=self.doc.id)
        rem = ClinicalReminder.query.filter_by(
            patient_id=self.pat.id, reminder_type='FOLLOWUP').first()
        self.assertEqual(rem.status, 'OPEN')
        # Mark the follow-up completed -> next scan closes the reminder.
        fu.status = 'Completed'
        fu.completed_at = utcnow()
        db.session.commit()
        scan_due_reminders(actor_id=self.doc.id)
        db.session.refresh(rem)
        self.assertEqual(rem.status, 'DONE')

    def test_open_reminders_for_patient_requires_scope(self):
        """Out-of-scope patients' reminders are never surfaced."""
        outsider = self._make_patient('outside@t.com')
        db.session.add(FollowUp(patient_id=outsider.id,
                                scheduled_for=utcnow() - timedelta(days=1),
                                reason='AFU', status='Scheduled'))
        db.session.commit()
        self._login()
        self.client.get('/clinical/reminders')
        from app.services.reminders import open_reminders_for_patients
        rems = open_reminders_for_patients(
            {self.pat.id})  # doctor's scoped ids only
        self.assertTrue(all(r.patient_id in {self.pat.id} for r in rems))


if __name__ == '__main__':
    unittest.main()
