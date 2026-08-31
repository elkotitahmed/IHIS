"""Admissions & bed management tests: admit, bed allocation, discharge, billing."""
import re
import unittest

from app import create_app, db
from app.models import (
    User, Role, Patient, Doctor, Specialty, Ward, Bed, Admission, Bill,
)

ROLES = ['SuperAdmin', 'Admin', 'Doctor', 'Nurse', 'Patient',
         'LabTechnician', 'Radiologist', 'Pharmacist', 'Receptionist',
         'Dentist', 'Physiotherapist']


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class AdmissionsTestCase(unittest.TestCase):
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

        self.doc = self._make_user('doctor@test.com', 'doctor', 'Doctor')
        self.pat = self._make_user('patient@test.com', 'patient', 'Patient')
        self.pat2 = self._make_user('patient2@test.com', 'patient', 'Patient')
        self.admin = self._make_user('admin@test.com', 'admin', 'Admin')
        self.nurse = self._make_user('nurse@test.com', 'nurse', 'Nurse')
        self.reception = self._make_user('recep@test.com', 'receptionist', 'Receptionist')

        self.patient = Patient.query.filter_by(user_id=self.pat.id).first()
        self.patient2 = Patient.query.filter_by(user_id=self.pat2.id).first()
        self.doctor = Doctor.query.filter_by(user_id=self.doc.id).first()

        self.ward = Ward(name='Ward A', ward_type='General', room_charge_per_day=100.0)
        db.session.add(self.ward); db.session.flush()
        self.bed = Bed(ward_id=self.ward.id, bed_no='B01')
        db.session.add(self.bed); db.session.commit()

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
        if utype == 'patient':
            db.session.add(Patient(user_id=u.id))
            db.session.commit()
        elif utype == 'doctor':
            spec = Specialty(name='General')
            db.session.add(spec); db.session.commit()
            db.session.add(Doctor(user_id=u.id, specialty_id=spec.id))
            db.session.commit()
        return u

    def _login(self, email, password='123456'):
        page = self.client.get('/auth/login')
        tok = _csrf(page.data)
        return self.client.post('/auth/login', data={
            'email': email, 'password': password, 'csrf_token': tok,
        }, follow_redirects=True)

    def _logout(self):
        self.client.get('/auth/logout')

    def _admit(self, patient_id):
        page = self.client.get('/admissions/admissions')
        tok = _csrf(page.data)
        return self.client.post('/admissions/admit', data={
            'csrf_token': tok, 'patient_id': str(patient_id),
            'ward_id': str(self.ward.id), 'bed_id': str(self.bed.id),
            'doctor_id': str(self.doctor.id), 'reason': 'Monitoring',
        }, follow_redirects=True)

    def test_receptionist_admit_occupies_bed(self):
        self._login(self.reception.email)
        self._admit(self.patient.id)
        admission = Admission.query.filter_by(patient_id=self.patient.id,
                                              status='Admitted').first()
        self.assertIsNotNone(admission)
        self.assertEqual(admission.bed.bed_no, 'B01')
        self.assertEqual(Bed.query.get(self.bed.id).status, 'Occupied')
        self.assertTrue(admission.admission_no.startswith('ADM-'))

    def test_double_admit_blocked(self):
        self._login(self.reception.email)
        self._admit(self.patient.id)
        self._admit(self.patient.id)  # second admit for same active patient
        count = Admission.query.filter_by(patient_id=self.patient.id,
                                          status='Admitted').count()
        self.assertEqual(count, 1)

    def test_occupied_bed_not_reusable(self):
        self._login(self.reception.email)
        self._admit(self.patient.id)
        # Patient 2 tries the same (now occupied) bed -> still error, no admit
        self._admit(self.patient2.id)
        self.assertIsNone(Admission.query.filter_by(patient_id=self.patient2.id).first())

    def test_discharge_frees_bed_and_generates_room_bill(self):
        self._login(self.reception.email)
        self._admit(self.patient.id)
        admission = Admission.query.filter_by(patient_id=self.patient.id).first()
        # admin discharges
        self._logout()
        self._login(self.admin.email)
        page = self.client.get(f'/admissions/admissions/{admission.id}/discharge')
        self.client.post(f'/admissions/admissions/{admission.id}/discharge', data={
            'csrf_token': _csrf(page.data), 'discharge_notes': 'Recovered',
            'discharge_diagnosis': 'Pneumonia', 'discharge_summary': 'Evolved well',
            'follow_up_instructions': 'Clinic review in 1 week',
        }, follow_redirects=True)
        db.session.refresh(admission)
        self.assertEqual(admission.status, 'Discharged')
        self.assertEqual(admission.discharge_diagnosis, 'Pneumonia')
        self.assertEqual(admission.follow_up_instructions, 'Clinic review in 1 week')
        self.assertEqual(Bed.query.get(self.bed.id).status, 'Available')
        # room bill created (at least 1 day)
        bill = Bill.query.filter_by(source_type='Room', source_id=admission.id).first()
        self.assertIsNotNone(bill)
        self.assertGreaterEqual(bill.total(), 100.0)

    def test_permission_gating(self):
        # Nurse has ADMISSION_VIEW but not ADMISSION_CREATE: can browse, cannot admit
        self._login(self.nurse.email)
        self.assertEqual(self.client.get('/admissions/dashboard').status_code, 200)
        # reception can admit but not discharge (discharge requires ADMISSION_DISCHARGE)
        self._logout()
        self._login(self.reception.email)
        self._admit(self.patient.id)
        admission = Admission.query.filter_by(patient_id=self.patient.id).first()
        page = self.client.get('/admissions/dashboard')
        self.client.post(f'/admissions/admissions/{admission.id}/discharge', data={
            'csrf_token': _csrf(page.data), 'discharge_notes': 'x',
        }, follow_redirects=True)
        db.session.refresh(admission)
        self.assertEqual(admission.status, 'Admitted')  # still admitted


if __name__ == '__main__':
    unittest.main()
