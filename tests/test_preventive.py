"""Preventive-care reminder engine tests.

Verifies the sweep generates alerts/tasks/notifications for overdue follow-ups,
missed appointments, and vaccines-due — and is idempotent (no duplicates on a
second run of the same data).
"""
import unittest
from datetime import datetime, timedelta

from app import create_app, db
from app.models import (Role, User, Patient, Doctor, Specialty, FollowUp,
                        ImmunizationRecord, Appointment, ClinicalAlert, Task,
                        Notification)
from seed import ROLES


class PreventiveSweepTest(unittest.TestCase):
    def test_sweep_generates_reminders_and_is_idempotent(self):
        app = create_app('testing')
        app.config['WTF_CSRF_ENABLED'] = False
        with app.app_context():
            db.create_all()
            for n in ROLES:
                db.session.add(Role(name=n))
            db.session.commit()

            pat_u = User(username='p', email='p@t.com', full_name='P',
                         user_type='patient')
            pat_u.set_password('123456')
            db.session.add(pat_u)
            db.session.flush()
            pat = Patient(user_id=pat_u.id)
            db.session.add(pat)
            db.session.flush()

            spec = Specialty(name='G')
            db.session.add(spec)
            db.session.flush()
            doc_u = User(username='d', email='d@t.com', full_name='D',
                         user_type='doctor')
            doc_u.set_password('123456')
            db.session.add(doc_u)
            db.session.flush()
            doc = Doctor(user_id=doc_u.id, specialty_id=spec.id)
            db.session.add(doc)
            db.session.flush()

            db.session.add(FollowUp(patient_id=pat.id, provider_id=doc.id,
                                    scheduled_for=datetime.now() - timedelta(days=1),
                                    reason='recheck', status='Scheduled'))
            db.session.add(FollowUp(patient_id=pat.id, provider_id=doc.id,
                                    scheduled_for=datetime.now() + timedelta(days=3),
                                    reason='review', status='Scheduled'))
            db.session.add(Appointment(patient_id=pat.id, doctor_id=doc.id,
                                       scheduled_at=datetime.now() - timedelta(days=1),
                                       status='Scheduled'))
            db.session.add(ImmunizationRecord(patient_id=pat.id, vaccine_name='HepB',
                                              dose_number=2,
                                              next_due=(datetime.now() - timedelta(days=2)).date()))
            db.session.commit()

            from app.services.preventive import run_preventive_sweep
            r = run_preventive_sweep()
            self.assertEqual(r['overdue_followup'], 1)
            self.assertEqual(r['missed_appointment'], 1)
            self.assertEqual(r['vaccine_due'], 1)
            self.assertEqual(r['upcoming_followup_reminder'], 1)

            # alerts / tasks / notification produced
            self.assertEqual(
                ClinicalAlert.query.filter_by(alert_type='OVERDUE_FOLLOWUP').count(), 1)
            self.assertEqual(
                ClinicalAlert.query.filter_by(alert_type='MISSED_APPOINTMENT').count(), 1)
            self.assertEqual(
                ClinicalAlert.query.filter_by(alert_type='VACCINE_DUE').count(), 1)
            # 2 tasks (follow-up + vaccine)
            self.assertEqual(Task.query.count(), 2)
            self.assertEqual(Notification.query.count(), 1)

            # idempotency: second run creates nothing
            r2 = run_preventive_sweep()
            self.assertEqual(r2['overdue_followup'], 0)
            self.assertEqual(r2['missed_appointment'], 0)
            self.assertEqual(r2['vaccine_due'], 0)
            self.assertEqual(r2['upcoming_followup_reminder'], 0)


if __name__ == '__main__':
    unittest.main()