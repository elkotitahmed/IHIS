"""Deterministic clinical-pharmacist review (app.services.medication_review):
renal function from the chart, renal dose rules, interactions against all
active medications, the colchicine + P-gp/CYP3A4 inhibitor contraindication
in renal impairment, drug-lab conflicts, alert generation at prescribing time
and the pharmacist's review card. No AI involved."""
import unittest
from datetime import date, datetime, timedelta

from app import create_app, db
from app.models import (Doctor, DrugInteraction, LabOrder, LabResult, LabTestCatalog, Medication, Patient,
                        Prescription, PrescriptionItem, Role, Specialty, User, VitalSign, ClinicalAlert)
from app.permissions import seed_permissions
from seed import ROLES


class Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context(); self.ctx.push()
        db.create_all()
        for n in ROLES:
            db.session.add(Role(name=n))
        db.session.commit()
        seed_permissions(db)
        self.client = self.app.test_client()
        spec = Specialty(name='Internal Medicine'); db.session.add(spec); db.session.flush()
        self.doc_user = self._user('doc', 'Doctor')
        self.doctor = Doctor(user_id=self.doc_user.id, specialty_id=spec.id); db.session.add(self.doctor)
        pu = self._user('robert', 'Patient', 'patient')
        self.patient = Patient(user_id=pu.id, date_of_birth=date.today() - timedelta(days=68 * 365 + 17), gender='Male',
                               allergies='NKDA')
        db.session.add(self.patient); db.session.flush()
        self.meds = {}
        for g, b in (('Colchicine', 'Colcrys'), ('Diltiazem', 'Cartia XT'), ('Apixaban', 'Eliquis'),
                     ('Lisinopril', 'Zestril'), ('Atorvastatin', 'Lipitor'), ('Metformin', 'Glucophage')):
            m = Medication(generic_name=g, brand_name=b); db.session.add(m); db.session.flush(); self.meds[g] = m
        db.session.add(DrugInteraction(medication_a_id=self.meds['Colchicine'].id, medication_b_id=self.meds['Diltiazem'].id,
                                       severity='Major', description='P-gp/CYP3A4 inhibition raises colchicine levels.',
                                       mechanism='PK', management='Reduce dose or avoid.'))
        self.tests = {}
        for name, unit, rng in (('Serum Creatinine', 'mg/dL', '0.7-1.3'), ('Serum Potassium', 'mEq/L', '3.5-5.0'),
                                ('eGFR (CKD-EPI)', 'mL/min/1.73m2', '90-150')):
            t = LabTestCatalog(test_name=name, unit=unit, normal_range=rng); db.session.add(t); db.session.flush()
            self.tests[name] = t
        db.session.commit()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def _user(self, uname, role, utype='doctor'):
        u = User(username=uname, email=f'{uname}@t.com', full_name=uname.title(), user_type=utype)
        u.set_password('123456'); u.roles.append(Role.query.filter_by(name=role).first())
        db.session.add(u); db.session.commit()
        return u

    def _login(self, u):
        self.client.post('/auth/login', data={'email': u.email, 'password': '123456'})

    def _lab(self, name, value, days_ago=0):
        o = LabOrder(patient_id=self.patient.id, doctor_id=self.doctor.id, test_id=self.tests[name].id, status='Verified',
                     order_date=datetime.utcnow() - timedelta(days=days_ago))
        db.session.add(o); db.session.flush()
        db.session.add(LabResult(order_id=o.id, result_value=str(value), result_unit=self.tests[name].unit,
                                 status='Verified', result_date=datetime.utcnow() - timedelta(days=days_ago)))
        db.session.commit()

    def _weight(self, kg):
        db.session.add(VitalSign(patient_id=self.patient.id, weight_kg=kg)); db.session.commit()

    def _rx(self, *lines, status='Active'):
        rx = Prescription(patient_id=self.patient.id, doctor_id=self.doctor.id, status=status)
        db.session.add(rx); db.session.flush()
        for g, dose in lines:
            db.session.add(PrescriptionItem(prescription_id=rx.id, medication_id=self.meds[g].id, dosage=dose, quantity=1))
        db.session.commit()
        return rx

    def _robert(self):
        """Robert Miller: 68 y, 82 kg, SCr 2.1 (baseline 1.8), K 5.2, home meds incl. diltiazem."""
        self._weight(82)
        self._lab('Serum Creatinine', 1.8, days_ago=90)
        self._lab('Serum Creatinine', 2.1)
        self._lab('eGFR (CKD-EPI)', 32)
        self._lab('Serum Potassium', 5.2)
        self._rx(('Apixaban', '5 mg'), ('Diltiazem', '240 mg'), ('Lisinopril', '20 mg'), ('Atorvastatin', '40 mg'))


class RenalFunctionTests(Base):
    def test_cockcroft_gault_uses_latest_creatinine_weight_age_sex(self):
        from app.services.medication_review import renal_function, cockcroft_gault
        self._robert()
        r = renal_function(self.patient)
        self.assertEqual(r['scr'], 2.1)                      # latest, not the 90-day-old baseline
        self.assertEqual(r['crcl'], cockcroft_gault(68, 82, 2.1))
        self.assertAlmostEqual(r['crcl'], 39.0, delta=0.6)
        self.assertEqual(r['method'], 'Cockcroft-Gault')
        self.assertEqual(r['potassium'], 5.2)
        self.assertTrue(r['stage'].startswith('G3b'))
        self.assertAlmostEqual(cockcroft_gault(68, 82, 2.1, female=True), cockcroft_gault(68, 82, 2.1) * 0.85, delta=0.1)

    def test_falls_back_to_reported_egfr_when_weight_missing(self):
        from app.services.medication_review import renal_function
        self._lab('Serum Creatinine', 2.1); self._lab('eGFR (CKD-EPI)', 32)
        r = renal_function(self.patient)
        self.assertIsNone(r['crcl']); self.assertEqual(r['effective'], 32); self.assertIn('weight', r['missing'])

    def test_umol_creatinine_is_converted(self):
        from app.services.medication_review import renal_function
        self.tests['Serum Creatinine'].unit = 'µmol/L'; db.session.commit()
        self._weight(82); self._lab('Serum Creatinine', 186)   # 186 µmol/L ≈ 2.1 mg/dL
        self.assertAlmostEqual(renal_function(self.patient)['crcl'], 39.0, delta=1.0)


class ReviewTests(Base):
    def test_colchicine_in_robert_is_contraindicated_with_management(self):
        from app.services.medication_review import review_prescription
        self._robert()
        rx = self._rx(('Colchicine', '1.2 mg then 0.6 mg'))
        rv = review_prescription(rx)
        self.assertEqual(rv['level'], 'CRITICAL')
        titles = [f['title'] for f in rv['findings']]
        top = rv['findings'][0]
        self.assertEqual(top['severity'], 'Contraindicated')
        self.assertIn('Colchicine + Diltiazem with reduced renal clearance', top['title'])
        self.assertIn('prednisone', top['management'].lower())
        # the plain formulary pair is superseded by the label contraindication (no duplicate alert)
        self.assertNotIn('Colchicine + Diltiazem', titles)
        self.assertTrue(any(f['type'] == 'RENAL' and 'dose adjustment' in f['title'] for f in rv['findings']))
        # background = the other active prescription's lines
        self.assertEqual({it.medication.generic_name for it in rv['background']},
                         {'Apixaban', 'Diltiazem', 'Lisinopril', 'Atorvastatin'})

    def test_interaction_checked_against_other_prescriptions_not_only_same_rx(self):
        from app.services.medication_review import review_prescription
        self._rx(('Diltiazem', '240 mg'))                       # older, separate prescription
        rx = self._rx(('Colchicine', '0.6 mg'))
        rv = review_prescription(rx)
        self.assertEqual(rv['level'], 'HIGH')                   # Major from the local formulary (no renal data)
        self.assertEqual(rv['findings'][0]['with'], 'Diltiazem')
        self.assertEqual(rv['findings'][0]['source'], 'Local formulary · PK')

    def test_apixaban_dose_reduction_criteria(self):
        from app.services.medication_review import review_prescription
        self._weight(82); self._lab('Serum Creatinine', 2.1)
        rx = self._rx(('Apixaban', '5 mg'))
        f = [f for f in review_prescription(rx)['findings'] if f['medication'] == 'Apixaban'][0]
        self.assertEqual(f['severity'], 'Info')                 # only 1 of 3 criteria -> no dose change
        self.patient.date_of_birth = date.today() - timedelta(days=82 * 365); db.session.commit()
        f = [f for f in review_prescription(rx)['findings'] if f['medication'] == 'Apixaban'][0]
        self.assertEqual(f['severity'], 'Major')                # age >= 80 + SCr >= 1.5 -> 2.5 mg BID
        self.assertIn('2.5 mg', f['management'])

    def test_potassium_rule_for_ace_inhibitor(self):
        from app.services.medication_review import review_prescription
        self._lab('Serum Potassium', 5.2)
        rx = self._rx(('Lisinopril', '20 mg'))
        f = review_prescription(rx)['findings'][0]
        self.assertEqual((f['type'], f['severity']), ('LAB', 'Moderate'))
        self._lab('Serum Potassium', 5.8)
        self.assertEqual(review_prescription(rx)['findings'][0]['severity'], 'Major')

    def test_metformin_contraindicated_below_30(self):
        from app.services.medication_review import review_prescription
        self._weight(60); self._lab('Serum Creatinine', 3.0)     # CrCl ~ 20 for a 68-year-old man
        rx = self._rx(('Metformin', '500 mg'))
        self.assertEqual(review_prescription(rx)['findings'][0]['severity'], 'Contraindicated')

    def test_clean_prescription_has_no_findings(self):
        from app.services.medication_review import review_prescription
        self._weight(82); self._lab('Serum Creatinine', 0.9)
        rx = self._rx(('Atorvastatin', '40 mg'))
        rv = review_prescription(rx)
        self.assertEqual((rv['level'], rv['findings']), ('OK', []))

    def test_patient_wide_review_dedupes_pairs(self):
        from app.services.medication_review import review_patient
        self._robert()
        rv = review_patient(self.patient)
        pairs = [tuple(sorted((f['medication'], f['with']))) for f in rv['findings'] if f['with']]
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertIn(('Apixaban', 'Diltiazem'), pairs)          # from DDInter (no local pair seeded here)


class AlertAndPageTests(Base):
    def test_prescribing_raises_one_alert_per_finding(self):
        from app.routes.doctor import flag_prescription_safety
        self._robert()
        rx = self._rx(('Colchicine', '1.2 mg'))
        flag_prescription_safety(rx); db.session.commit()
        alerts = ClinicalAlert.query.filter_by(patient_id=self.patient.id, source_type='prescription', source_id=rx.id).all()
        self.assertGreaterEqual(len(alerts), 2)
        self.assertEqual(max(a.severity for a in alerts if a.alert_type == 'DRUG_INTERACTION'), 'CRITICAL')
        self.assertTrue(any(a.alert_type == 'DRUG_DISEASE' for a in alerts))
        n = len(alerts)
        flag_prescription_safety(rx); db.session.commit()      # idempotent
        self.assertEqual(ClinicalAlert.query.filter_by(source_id=rx.id, source_type='prescription').count(), n)

    def test_pharmacist_page_shows_review_card_and_prefill(self):
        self._robert()
        rx = self._rx(('Colchicine', '1.2 mg'))
        self._login(self._user('pharma', 'Pharmacist', 'pharmacist'))
        r = self.client.get(f'/pharmacy/prescriptions/{rx.id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Clinical pharmacist review', r.data)
        self.assertIn(b'contact the prescriber', r.data)
        self.assertIn(b'Cockcroft-Gault', r.data)
        self.assertIn(b'rv-prefill', r.data)
        self.assertIn(b'data-category="CONTRAINDICATION"', r.data)
        self.assertIn(b'id="interventionForm"', r.data)

    def test_workbench_shows_rules_verdict(self):
        self._robert()
        self._rx(('Colchicine', '1.2 mg'))
        self._login(self._user('pharma2', 'Pharmacist', 'pharmacist'))
        r = self.client.get('/pharmacy/ai-workbench')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Rules verdict', r.data)
        self.assertIn(b'>CRITICAL<', r.data)

    def test_ai_review_page_shows_rules_without_calling_gemini(self):
        self._robert()
        self._login(self._user('pharma3', 'Pharmacist', 'pharmacist'))
        r = self.client.get(f'/ai/medication-review/{self.patient.id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Clinical pharmacist review', r.data)
        self.assertIn(b'Apixaban + Diltiazem', r.data)


if __name__ == '__main__':
    unittest.main()
