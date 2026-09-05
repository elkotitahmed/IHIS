"""Tests for the verified Clinical Summary page and the patient safety strip."""
import re
import unittest

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, CareTeam,
                        CareTeamMember, MedicalRecord, Allergy, Problem,
                        LabTestCatalog, LabOrder, LabResult, VitalSign, utcnow)


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class ClinicalSummaryTestCase(unittest.TestCase):
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

    def test_summary_renders_verified_data(self):
        self._login()
        db.session.add(Allergy(patient_id=self.pat.id, substance='Penicillin',
                               severity='Severe', status='Active'))
        db.session.add(Problem(patient_id=self.pat.id, description='Hypertension',
                               icd10_code='I10', status='Active'))
        db.session.add(VitalSign(patient_id=self.pat.id,
                                 blood_pressure_systolic=150,
                                 blood_pressure_diastolic=90, heart_rate=88,
                                 temperature=37.0, oxygen_saturation=96,
                                 recorded_at=utcnow()))
        order = LabOrder(patient_id=self.pat.id, doctor_id=self.doc_profile.id,
                         test_id=self.test.id, status='FINALIZED')
        db.session.add(order)
        db.session.flush()
        db.session.add(LabResult(order_id=order.id, result_value='13.0',
                                 status='Verified', result_date=utcnow()))
        db.session.commit()

        r = self.client.get(f'/clinical/summary/{self.pat.id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Penicillin', r.data)
        self.assertIn(b'Hypertension', r.data)
        self.assertIn(b'150', r.data)
        self.assertIn(b'CBC', r.data)

    def test_summary_shows_safety_strip(self):
        """The patient 360 page embeds the safety strip with allergy/problem chips."""
        self._login()
        db.session.add(Allergy(patient_id=self.pat.id, substance='Aspirin',
                               severity='Moderate', status='Active'))
        db.session.add(Problem(patient_id=self.pat.id, description='Diabetes',
                               icd10_code='E11', status='Active'))
        db.session.commit()
        r = self.client.get(f'/clinical/patient/{self.pat.id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Aspirin', r.data)
        self.assertIn(b'Diabetes', r.data)
        self.assertIn(b'patient-safety-strip', r.data)

    def test_summary_blocked_out_of_scope(self):
        """A doctor cannot open a summary for a patient they have no relation to."""
        self._login()
        outsider = self._make_patient('outside@t.com')
        r = self.client.get(f'/clinical/summary/{outsider.id}')
        self.assertEqual(r.status_code, 403)

    def test_summary_link_from_patient_360(self):
        self._login()
        db.session.add(MedicalRecord(patient_id=self.pat.id,
                                     doctor_id=self.doc_profile.id,
                                     diagnosis='Cough', status='Signed'))
        db.session.commit()
        r = self.client.get(f'/clinical/patient/{self.pat.id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'/clinical/summary/', r.data)

    def test_summary_shows_care_team_and_print(self):
        """Care team members and a print action surface on the summary."""
        self._login()
        r = self.client.get(f'/clinical/summary/{self.pat.id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Care Team', r.data)
        self.assertIn(b'Test doctor', r.data)
        self.assertIn(b'Physician', r.data)
        self.assertIn(b'window.print()', r.data)
        self.assertIn(b'@media print', r.data)


if __name__ == '__main__':
    unittest.main()
