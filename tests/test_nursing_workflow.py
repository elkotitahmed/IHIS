"""Nursing MAR + Intake/Output workflow integration tests."""
import re
import unittest

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, Medication,
                        Prescription, PrescriptionItem, MedicationAdministration,
                        IntakeOutput, CareTeam, CareTeamMember, Appointment,
                        Notification, utcnow)


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class NursingWorkflowTestCase(unittest.TestCase):
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
        db.session.commit()
        from app.permissions import seed_permissions
        seed_permissions(db)
        self.doc = self._make_user('docn@t.com', 'doctor', 'Doctor')
        self.doc_u = User.query.filter_by(email='docn@t.com').first()
        self.nurse = self._make_user('nur@t.com', 'nurse', 'Nurse')
        self.nurse_u = User.query.filter_by(email='nur@t.com').first()
        self.pat = User(username='patn', email='patn@t.com', full_name='NR P',
                        user_type='patient')
        self.pat.set_password('123456')
        self.pat.roles.append(Role.query.filter_by(name='Patient').first())
        db.session.add(self.pat)
        db.session.commit()
        self.patient = Patient(user_id=self.pat.id)
        db.session.add(self.patient)
        db.session.commit()
        self.med = Medication(generic_name='Amoxicillin', brand_name='Amoxil')
        db.session.add(self.med)
        db.session.commit()
        # Care team: nurse needs need-to-know on this patient.
        team = CareTeam(patient_id=self.patient.id, name='Ward')
        db.session.add(team)
        db.session.commit()
        db.session.add(CareTeamMember(team_id=team.id, user_id=self.nurse_u.id,
                                      role='Primary Nurse'))
        db.session.commit()
        # Prescription by the doctor with one item.
        self.rx = Prescription(patient_id=self.patient.id,
                               doctor_id=Doctor.query.filter_by(
                                   user_id=self.doc_u.id).first().id)
        db.session.add(self.rx)
        db.session.commit()
        self.item = PrescriptionItem(prescription_id=self.rx.id,
                                     medication_id=self.med.id, dosage='500mg',
                                     frequency='8h', duration='7 days',
                                     instructions='before food', quantity='21')
        db.session.add(self.item)
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
            spec = Specialty(name='Internal')
            db.session.add(spec)
            db.session.commit()
            db.session.add(Doctor(user_id=u.id, specialty_id=spec.id))
        db.session.commit()
        return u

    def _login(self, email):
        self.client.get('/auth/logout')
        page = self.client.get('/auth/login')
        tok = _csrf(page.data)
        return self.client.post('/auth/login', data={
            'email': email, 'password': '123456', 'csrf_token': tok,
        }, follow_redirects=True)

    def _schedule_dose(self, scheduled='2026-09-01T08:00'):
        self._login(self.nurse_u.email)
        page = self.client.get(f'/nursing/patients/{self.patient.id}/mar')
        tok = _csrf(page.data)
        return self.client.post(f'/nursing/patients/{self.patient.id}/mar', data={
            'prescription_item_id': str(self.item.id),
            'scheduled_time': scheduled,
            'dose_given': '500mg', 'route': 'Oral',
            'csrf_token': tok,
        }, follow_redirects=True)

    def test_schedule_and_administer(self):
        self._schedule_dose()
        admin = MedicationAdministration.query.filter_by(
            prescription_item_id=self.item.id).first()
        self.assertIsNotNone(admin)
        self.assertEqual(admin.status, 'Scheduled')
        # mark Administered
        self._login(self.nurse_u.email)
        self.client.post(f'/nursing/administration/{admin.id}/outcome', data={
            'status': 'Administered', 'csrf_token': _csrf(
                self.client.get(f'/nursing/patients/{self.patient.id}/mar').data),
        }, follow_redirects=True)
        admin = db.session.get(MedicationAdministration, admin.id)
        self.assertEqual(admin.status, 'Administered')
        self.assertIsNotNone(admin.administered_at)
        # patient notified
        n = Notification.query.filter_by(user_id=self.pat.id,
                                          entity_type='prescription',
                                          entity_id=self.rx.id).first()
        self.assertIsNotNone(n)
        self.assertIn('administered', n.title.lower())

    def test_refused_notifies_doctor(self):
        self._schedule_dose()
        admin = MedicationAdministration.query.filter_by(
            prescription_item_id=self.item.id).first()
        self._login(self.nurse_u.email)
        self.client.post(f'/nursing/administration/{admin.id}/outcome', data={
            'status': 'Refused', 'reason': 'Patient allergic reaction',
            'csrf_token': _csrf(
                self.client.get(f'/nursing/patients/{self.patient.id}/mar').data),
        }, follow_redirects=True)
        admin = db.session.get(MedicationAdministration, admin.id)
        self.assertEqual(admin.status, 'Refused')
        self.assertEqual(admin.reason, 'Patient allergic reaction')
        n = Notification.query.filter_by(user_id=self.doc_u.id,
                                          entity_type='prescription',
                                          entity_id=self.rx.id).first()
        self.assertIsNotNone(n)
        self.assertIn('refused', n.title.lower())

    def test_intake_output_totals(self):
        self._login(self.nurse_u.email)
        page = self.client.get(f'/nursing/patients/{self.patient.id}/intake-output')
        tok = _csrf(page.data)
        self.client.post(f'/nursing/patients/{self.patient.id}/intake-output', data={
            'intake_type': 'Oral', 'intake_ml': '1500',
            'output_type': 'Urine', 'output_ml': '900',
            'notes': 'morning', 'csrf_token': tok,
        }, follow_redirects=True)
        self.client.post(f'/nursing/patients/{self.patient.id}/intake-output', data={
            'intake_type': 'IV', 'intake_ml': '500',
            'output_type': '', 'output_ml': '',
            'csrf_token': tok,
        }, follow_redirects=True)
        recs = IntakeOutput.query.filter_by(patient_id=self.patient.id).all()
        self.assertEqual(len(recs), 2)
        total_in = sum(r.intake_ml or 0 for r in recs)
        total_out = sum(r.output_ml or 0 for r in recs)
        self.assertEqual(total_in, 2000)
        self.assertEqual(total_out, 900)
        self.assertEqual(total_in - total_out, 1100)


if __name__ == '__main__':
    unittest.main()
