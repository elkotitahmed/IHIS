"""FHIR R4 read feed (gap #18): Patient resource, $everything bundle, browser view."""
import json
import re
import unittest
from datetime import timedelta

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, LabTestCatalog,
                        LabOrder, LabResult, Diagnosis, Problem, Allergy,
                        ImmunizationRecord, Medication, Prescription,
                        PrescriptionItem, Ward, Bed, Admission, Appointment)
from app.utils import utcnow

ROLES = ['SuperAdmin', 'Admin', 'Doctor', 'Nurse', 'Patient',
         'LabTechnician', 'Radiologist', 'Pharmacist', 'Receptionist',
         'Dentist', 'Physiotherapist']


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class FhirViewTestCase(unittest.TestCase):
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

        self.spec = Specialty(name='General')
        db.session.add(self.spec)
        self.test = LabTestCatalog(test_name='CBC', unit='x10^9/L',
                                   normal_range='4.5 - 11.0', is_active=True)
        db.session.add(self.test)
        self.med = Medication(generic_name='Aspirin', brand_name='Aspirin')
        db.session.add(self.med)
        db.session.flush()

        self.doc = self._make_user('doc@t.com', 'doctor', 'Doctor')
        self.doc_profile = Doctor.query.filter_by(user_id=self.doc.id).first()
        self.doc2 = self._make_user('doc2@t.com', 'doctor', 'Doctor')
        self.pat = self._make_patient('pat@t.com')

        now = utcnow()

        # lab order + verified result (also grants the doctor need-to-know)
        order = LabOrder(patient_id=self.pat.id, doctor_id=self.doc_profile.id,
                         test_id=self.test.id, status='VERIFIED',
                         accession_number='ACC-1')
        db.session.add(order)
        db.session.flush()
        db.session.add(LabResult(order_id=order.id, result_value='14.2',
                                 result_unit='x10^9/L', is_abnormal=True,
                                 is_critical=True, status='Verified'))
        db.session.add(Diagnosis(patient_id=self.pat.id,
                                 doctor_id=self.doc_profile.id,
                                 icd10_code='I10',
                                 description='Essential Hypertension',
                                 is_primary=True))
        db.session.add(Problem(patient_id=self.pat.id, icd10_code='E11',
                               description='Type 2 diabetes', status='Active',
                               severity='Moderate'))
        db.session.add(Allergy(patient_id=self.pat.id, substance='Penicillin',
                               reaction='Rash', severity='Severe', verified=True))
        db.session.add(ImmunizationRecord(patient_id=self.pat.id,
                                          vaccine_name='Hepatitis B',
                                          dose_number=1, lot_number='LOT1'))
        rx = Prescription(patient_id=self.pat.id, doctor_id=self.doc_profile.id,
                          status='Active')
        db.session.add(rx)
        db.session.flush()
        db.session.add(PrescriptionItem(prescription_id=rx.id,
                                        medication_id=self.med.id,
                                        dosage='81 mg', frequency='daily',
                                        duration='30 days', quantity=30))
        ward = Ward(name='Ward A', ward_type='General')
        db.session.add(ward)
        db.session.flush()
        bed = Bed(ward_id=ward.id, bed_no='B99')
        db.session.add(bed)
        db.session.add(Admission(patient_id=self.pat.id, ward_id=ward.id,
                                 bed_id=bed.id, admission_no='ADM-7777',
                                 admitted_at=now - timedelta(days=1),
                                 status='Admitted', reason='Chest pain'))
        db.session.add(Appointment(patient_id=self.pat.id,
                                   doctor_id=self.doc_profile.id,
                                   scheduled_at=now + timedelta(days=2),
                                   status='Scheduled', reason='Review'))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _make_user(self, email, utype, role, password='123456'):
        u = User(username=email.split('@')[0], email=email,
                 full_name='Dr ' + utype, user_type=utype)
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
                 full_name='Pat Fhir', user_type='patient')
        u.set_password('123456')
        u.roles.append(Role.query.filter_by(name='Patient').first())
        db.session.add(u)
        db.session.commit()
        p = Patient(user_id=u.id, mrn='MRN-F', date_of_birth=utcnow().date() -
                    timedelta(days=365 * 40), gender='male')
        db.session.add(p)
        db.session.commit()
        return p

    def _login(self, email, password='123456'):
        self.client.get('/auth/logout')
        r = self.client.get('/auth/login')
        return self.client.post('/auth/login', data={
            'email': email, 'password': password, 'csrf_token': _csrf(r.data)},
            follow_redirects=True)

    def test_patient_resource(self):
        self._login('doc@t.com')
        r = self.client.get(f'/fhir/patients/{self.pat.id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn('application/fhir+json', r.content_type)
        body = json.loads(r.data)
        self.assertEqual(body['resourceType'], 'Patient')
        self.assertEqual(body['id'], str(self.pat.id))
        self.assertEqual(body['name'][0]['family'], 'Fhir')
        self.assertEqual(body['gender'], 'male')

    def test_everything_bundle(self):
        self._login('doc@t.com')
        r = self.client.get(f'/fhir/patients/{self.pat.id}/$everything')
        self.assertEqual(r.status_code, 200)
        self.assertIn('application/fhir+json', r.content_type)
        body = json.loads(r.data)
        self.assertEqual(body['resourceType'], 'Bundle')
        types = {e['resource']['resourceType'] for e in body['entry']}
        for expected in ('Patient', 'Observation', 'DiagnosticReport', 'Condition',
                         'AllergyIntolerance', 'Immunization', 'MedicationRequest',
                         'Encounter'):
            self.assertIn(expected, types)
        self.assertEqual(body['total'], len(body['entry']))
        obs = next(e['resource'] for e in body['entry']
                   if e['resource']['resourceType'] == 'Observation')
        self.assertEqual(obs['valueQuantity']['value'], 14.2)
        self.assertEqual(obs['valueQuantity']['unit'], 'x10^9/L')
        self.assertIn('referenceRange', obs)

    def test_browser_view(self):
        self._login('doc@t.com')
        r = self.client.get(f'/fhir/patients/{self.pat.id}/view')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Pat Fhir', r.data)
        for expected in ('Observation', 'Condition', 'MedicationRequest', 'Encounter'):
            self.assertIn(expected.encode(), r.data)

    def test_no_access_is_403(self):
        self._login('doc2@t.com')
        for path in (f'/fhir/patients/{self.pat.id}',
                     f'/fhir/patients/{self.pat.id}/$everything',
                     f'/fhir/patients/{self.pat.id}/view'):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 403, path)

    def test_patient_index(self):
        self._login('doc@t.com')
        r = self.client.get('/fhir/patients')
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.data)
        self.assertEqual(body['resourceType'], 'Bundle')
        self.assertGreaterEqual(body['total'], 1)
        self.assertIn(b'MRN-F', r.data)


if __name__ == '__main__':
    unittest.main()