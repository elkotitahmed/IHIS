"""Explainable health-insights profile and the platform capabilities catalogue."""
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from app import create_app, db
from app.models import (Doctor, DrugInteraction, LabOrder, LabResult, LabTestCatalog, Medication, Patient,
                        Prescription, PrescriptionItem, Problem, Role, Specialty, User, VitalSign)
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

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def _user(self, uname, role, utype):
        u = User(username=uname, email=f'{uname}@t.com', full_name=uname.title(), user_type=utype)
        u.set_password('123456'); u.roles.append(Role.query.filter_by(name=role).first())
        db.session.add(u); db.session.commit()
        return u

    def _login(self, u):
        self.client.post('/auth/login', data={'email': u.email, 'password': '123456'})

    def _robert(self):
        pu = self._user('robert', 'Patient', 'patient')
        p = Patient(user_id=pu.id, date_of_birth=date.today() - timedelta(days=68 * 365 + 20), gender='Male',
                    allergies='NKDA (No Known Drug Allergies)', chronic_diseases='AF; CKD 3b; hypertension')
        db.session.add(p); db.session.flush()
        spec = Specialty(name='Internal Medicine'); db.session.add(spec); db.session.flush()
        du = self._user('doc', 'Doctor', 'doctor'); d = Doctor(user_id=du.id, specialty_id=spec.id); db.session.add(d); db.session.flush()
        for code, desc in (('I48.91', 'Non-valvular atrial fibrillation'), ('N18.32', 'Chronic kidney disease, stage 3b'),
                           ('I10', 'Essential hypertension')):
            db.session.add(Problem(patient_id=p.id, icd10_code=code, description=desc, status='Active'))
        db.session.add(VitalSign(patient_id=p.id, weight_kg=82, blood_pressure_systolic=146, blood_pressure_diastolic=88,
                                 heart_rate=105, respiratory_rate=18, oxygen_saturation=96, temperature=37.8,
                                 recorded_at=datetime.utcnow()))
        for name, unit, rng, val in (('Serum Creatinine', 'mg/dL', '0.7-1.3', '2.1'), ('Serum Potassium', 'mEq/L', '3.5-5.0', '5.2')):
            t = LabTestCatalog(test_name=name, unit=unit, normal_range=rng); db.session.add(t); db.session.flush()
            o = LabOrder(patient_id=p.id, doctor_id=d.id, test_id=t.id, status='Verified'); db.session.add(o); db.session.flush()
            db.session.add(LabResult(order_id=o.id, result_value=val, result_unit=unit, is_abnormal=True, status='Verified'))
        meds = {}
        for g in ('Apixaban', 'Diltiazem', 'Lisinopril', 'Atorvastatin', 'Colchicine'):
            m = Medication(generic_name=g); db.session.add(m); db.session.flush(); meds[g] = m
        db.session.add(DrugInteraction(medication_a_id=meds['Colchicine'].id, medication_b_id=meds['Diltiazem'].id,
                                       severity='Major', description='x', management='y'))
        rx = Prescription(patient_id=p.id, doctor_id=d.id, status='Active'); db.session.add(rx); db.session.flush()
        for g in meds:
            db.session.add(PrescriptionItem(prescription_id=rx.id, medication_id=meds[g].id, dosage='1', quantity=1))
        db.session.commit()
        return p, pu

    def _healthy(self):
        pu = self._user('healthy', 'Patient', 'patient')
        p = Patient(user_id=pu.id, date_of_birth=date(1992, 1, 1), gender='Male', allergies='NKDA')
        db.session.add(p); db.session.commit()
        return p, pu


class ProfileTests(Base):
    def test_nkda_is_not_a_risk_factor(self):
        from app.services.health_insights import build
        p, _ = self._healthy()
        prof = build(p)
        self.assertEqual(prof['score'], 0); self.assertEqual(prof['level'], 'Low')
        self.assertEqual(prof['allergies'], [])
        self.assertFalse(any('allerg' in f['label'].lower() for f in prof['factors']))
        # legacy heuristic agrees now
        from app.services.ai.ai_interfaces import AIClinicalAssistant
        flags = AIClinicalAssistant().analyze_patient(p.id)['flags']
        self.assertFalse(any(f['title'] == 'Documented Allergies' for f in flags))

    def test_robert_profile_is_explainable(self):
        from app.services.health_insights import build
        p, _ = self._robert()
        prof = build(p)
        labels = [f['label'] for f in prof['factors']]
        self.assertIn('Age 65–74', labels)
        self.assertTrue(any('renal impairment' in l for l in labels))
        self.assertIn('Hyperkalaemia', labels)
        self.assertIn('Polypharmacy (5+ medications)', labels)
        self.assertIn('High-risk medication class', labels)                  # apixaban
        self.assertIn('Medication safety finding', labels)                   # colchicine + diltiazem
        self.assertTrue(any('NEWS2' in l for l in labels))
        self.assertNotIn('Documented drug allergy', labels)                  # NKDA
        self.assertEqual(prof['level'], 'High')
        self.assertTrue(all(f['evidence'] and f['source'] for f in prof['factors']))
        titles = [o['title'] for o in prof['observations']]
        self.assertIn('Reduced kidney function', titles)
        self.assertIn('Potassium above range', titles)
        self.assertTrue(all(o['source'] for o in prof['observations']))

    def test_page_renders_without_calling_gemini(self):
        p, pu = self._robert()
        self._login(pu)
        with mock.patch('app.services.ai.ai_risk_prediction.AIPatientRiskPrediction.predict_risk') as pr:
            r = self.client.get('/ai/health-insights')
            pr.assert_not_called()
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Contributing factors', r.data)
        self.assertIn(b'Reduced kidney function', r.data)
        self.assertIn(b'NKDA', r.data)
        self.assertNotIn(b'Documented Allergies', r.data)

    def test_admin_picker_and_ai_on_request_only(self):
        p, _ = self._robert()
        self._login(self._user('adm', 'Admin', 'admin'))
        with mock.patch('app.services.ai.ai_risk_prediction.AIPatientRiskPrediction.predict_risk',
                        return_value={'source': 'gemini', 'narrative': 'AI text here', 'mitigation_strategies': ['m1'],
                                      'score': 1, 'level': 'Low', 'reasons': []}) as pr:
            r = self.client.get(f'/ai/health-insights?pid={p.id}&ai=1')
            pr.assert_called_once()
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'AI text here', r.data)


class CapabilitiesTests(Base):
    def test_every_capability_url_resolves(self):
        from app.routes.super_admin import capabilities
        adapter = self.app.url_map.bind('localhost')
        self._login(self._user('sa', 'SuperAdmin', 'admin'))
        r = self.client.get('/super-admin/capabilities')
        self.assertEqual(r.status_code, 200)
        html = r.data.decode()
        import re
        urls = sorted(set(re.findall(r'class="cap-card" href="([^"]+)"', html)))
        self.assertGreater(len(urls), 20)
        for u in urls:
            try:
                adapter.match(u.split('?')[0])
            except Exception as e:  # noqa: BLE001
                self.fail(f'{u}: {type(e).__name__}')
        for name in ('Chest X-ray Screening', 'Fracture Detection', 'Tooth Segmentation', 'Skin Lesion Detection'):
            self.assertIn(name, html)
        self.assertIn('capSearch', html)


if __name__ == '__main__':
    unittest.main()
