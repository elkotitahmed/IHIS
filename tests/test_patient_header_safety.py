"""Tests for the reusable patient header + safety strip surfaced across all
single-patient clinical pages (doctor, AI, pharmacy, nursing, dentistry).

Harvest reference: OpenMRS chart banner / FHIR PatientBanner. The patient
header partial reads the PatientSafetyContext keys (allergies, problems,
open_alerts, active_meds) that every clinical route now passes.
"""
import re
import unittest

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, CareTeam,
                        CareTeamMember, Allergy, Problem, LabTestCatalog,
                        LabOrder, LabResult, Prescription, PrescriptionItem,
                        MedicalRecord, Medication, DentalChart)


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


ROLES = ['SuperAdmin', 'Admin', 'Doctor', 'Nurse', 'Patient',
         'LabTechnician', 'Radiologist', 'Pharmacist', 'Receptionist',
         'Dentist', 'Physiotherapist']

HEADER_MARK = b'patient-header-main'
STRIP_MARK = b'patient-safety-strip'


class PatientHeaderSafetyTestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for n in ROLES:
            db.session.add(Role(name=n))
        from app.permissions import seed_permissions
        seed_permissions(db)
        self.spec = Specialty(name='General')
        db.session.add(self.spec)
        self.test = LabTestCatalog(test_name='CBC', is_active=True)
        self.med = Medication(generic_name='Metformin',
                              brand_name='Metformin',
                              category='Antidiabetic', is_active=True)
        db.session.add(self.test)
        db.session.add(self.med)
        db.session.flush()

        self.usernames = {}
        for name, role in (('doctor', 'Doctor'), ('nurse', 'Nurse'),
                           ('pharm', 'Pharmacist'), ('dent', 'Dentist'),
                           ('physio', 'Physiotherapist')):
            u = User(username=name, email=f'{name}@t.com',
                     full_name=name.title(), user_type=name)
            u.set_password('123456')
            u.roles.append(Role.query.filter_by(name=role).first())
            db.session.add(u)
            db.session.flush()
            if role == 'Doctor':
                db.session.add(Doctor(user_id=u.id, specialty_id=self.spec.id,
                                      license_number='L' + name))
            self.usernames[name] = u
        db.session.commit()

        self.doc_profile = Doctor.query.filter_by(
            user_id=self.usernames['doctor'].id).first()
        self.pat = self._make_patient('pat@t.com', 'MRN-001')
        self.outsider = self._make_patient('out@t.com', 'MRN-002')
        team = CareTeam(patient_id=self.pat.id, name='Care')
        db.session.add(team)
        db.session.flush()
        for name in ('doctor', 'nurse', 'pharm', 'dent', 'physio'):
            db.session.add(CareTeamMember(
                team_id=team.id, user_id=self.usernames[name].id,
                role='Member'))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _make_patient(self, email, mrn):
        u = User(username=email.split('@')[0], email=email,
                 full_name='Pat ' + email[:3], user_type='patient')
        u.set_password('123456')
        u.roles.append(Role.query.filter_by(name='Patient').first())
        db.session.add(u)
        db.session.commit()
        p = Patient(user_id=u.id, mrn=mrn)
        db.session.add(p)
        db.session.commit()
        return p

    def _login(self, key):
        email = self.usernames[key].email
        self.client.get('/auth/logout')
        r = self.client.get('/auth/login')
        return self.client.post('/auth/login', data={
            'email': email, 'password': '123456',
            'csrf_token': _csrf(r.data)}, follow_redirects=True)

    def _seed_safety(self):
        db.session.add(Allergy(patient_id=self.pat.id, substance='Penicillin',
                               severity='Severe', status='Active'))
        db.session.add(Allergy(patient_id=self.pat.id, substance='Latex',
                               severity='Moderate', status='Active'))
        db.session.add(Problem(patient_id=self.pat.id, description='Hypertension',
                               icd10_code='I10', status='Active'))
        db.session.commit()

    def _med_record(self):
        rec = MedicalRecord(patient_id=self.pat.id,
                            doctor_id=self.doc_profile.id,
                            diagnosis='Hypertension', status='Draft')
        db.session.add(rec)
        db.session.commit()
        return rec

    def _lab(self):
        order = LabOrder(patient_id=self.pat.id, doctor_id=self.doc_profile.id,
                         test_id=self.test.id, status='FINALIZED')
        db.session.add(order)
        db.session.flush()
        db.session.add(LabResult(order_id=order.id, result_value='13.0',
                                 status='Verified'))
        db.session.commit()
        return order

    def _rx(self):
        rx = Prescription(patient_id=self.pat.id, doctor_id=self.doc_profile.id,
                          status='Active')
        db.session.add(rx)
        db.session.flush()
        db.session.add(PrescriptionItem(prescription_id=rx.id,
                                        medication_id=self.med.id,
                                        dosage='500mg', frequency='BID',
                                        duration='30 days'))
        db.session.commit()
        return rx

    def _run(self, key, url, expect_mark=HEADER_MARK):
        self._login(key)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, f'{url} -> {r.status_code}')
        self.assertIn(expect_mark, r.data, f'{url} missing safety header')
        self.assertIn(b'Penicillin', r.data, f'{url} missing allergy chip')
        self.assertIn(b'Hypertension', r.data, f'{url} missing problem chip')

    def test_doctor_pages_all_show_header(self):
        self._seed_safety()
        rec = self._med_record()
        self._lab()
        for url in (f'/doctor/patients/{self.pat.id}/overview',
                    f'/doctor/patients/{self.pat.id}',
                    f'/doctor/patients/{self.pat.id}/emr/add',
                    f'/doctor/records/{rec.id}/edit',
                    f'/doctor/patients/{self.pat.id}/prescriptions',
                    f'/doctor/patients/{self.pat.id}/lab-order',
                    f'/doctor/patients/{self.pat.id}/radiology-order'):
            self._run('doctor', url)

    def test_doctor_360_shows_safety_strip(self):
        self._seed_safety()
        self._run('doctor', f'/doctor/patients/{self.pat.id}/360',
                  expect_mark=STRIP_MARK)

    def test_ai_pages_all_show_header(self):
        self._seed_safety()
        self._lab()
        self._rx()
        for url in (f'/ai/summary/{self.pat.id}',
                    f'/ai/diagnosis-support/{self.pat.id}',
                    f'/ai/medication-review/{self.pat.id}',
                    f'/ai/soap-notes/{self.pat.id}',
                    f'/ai/smart-orders/{self.pat.id}',
                    f'/ai/patient-communication/{self.pat.id}',
                    f'/ai/medical-coding/{self.pat.id}'):
            self._run('doctor', url)

    def test_ai_rehab_shows_header(self):
        self._seed_safety()
        self._run('physio', f'/ai/rehab/{self.pat.id}')

    def test_pharmacy_reconcile_shows_header(self):
        self._seed_safety()
        self._run('pharm', f'/pharmacy/patient/{self.pat.id}/reconcile')

    def test_nursing_mar_shows_header(self):
        self._seed_safety()
        self._rx()
        self._run('nurse', f'/nursing/patients/{self.pat.id}/mar')

    def test_dentistry_pages_all_show_header(self):
        self._seed_safety()
        db.session.add(DentalChart(patient_id=self.pat.id, tooth_number='11',
                                   numbering_system='FDI', status='Healthy'))
        db.session.commit()
        for url in (f'/dentistry/patients/{self.pat.id}/chart',
                    f'/dentistry/patients/{self.pat.id}/record',
                    f'/dentistry/patients/{self.pat.id}/procedure',
                    f'/dentistry/patients/{self.pat.id}/imaging',
                    f'/dentistry/patients/{self.pat.id}/treatment-plan'):
            self._run('dent', url)

    def test_header_not_leaked_out_of_scope(self):
        self._login('doctor')
        r = self.client.get(f'/doctor/patients/{self.outsider.id}/overview')
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(HEADER_MARK, r.data)

    def test_add_emr_page_safety_visible_before_acting(self):
        """Allergy chips precede the record form so a clinician sees them
        before documenting an encounter."""
        self._seed_safety()
        self._login('doctor')
        r = self.client.get(f'/doctor/patients/{self.pat.id}/emr/add')
        header = r.data.find(b'patient-header-main')
        form = r.data.find(b'name="diagnosis"')
        self.assertGreater(header, -1)
        self.assertGreater(form, -1)
        self.assertLess(header, form)


if __name__ == '__main__':
    unittest.main()