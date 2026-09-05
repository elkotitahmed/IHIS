"""Lab critical (panic) value auto-flagging and escalation tests."""
import re
import unittest
from datetime import datetime

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, LabTestCatalog,
                        LabOrder, LabResult, ClinicalAlert, Notification)


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class LabCriticalTestCase(unittest.TestCase):
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
        db.session.commit()

        self.doc = self._make_user('doc@t.com', 'doctor', 'Doctor')
        self.doc_profile = Doctor.query.filter_by(user_id=self.doc.id).first()
        self.pat = self._make_patient('pat@t.com')
        self.lab = self._make_user('lab@t.com', 'lab_technician', 'LabTechnician')
        self.admin = self._make_user('adm@t.com', 'admin', 'Admin')
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

    def _make_test(self, **kw):
        t = LabTestCatalog(test_name='WBC', category='Hematology',
                           normal_range='4.0-11.0', unit='x10^9/L', **kw)
        db.session.add(t)
        db.session.commit()
        return t

    def _make_order(self, test_id):
        o = LabOrder(patient_id=self.pat.id, doctor_id=self.doc_profile.id,
                     test_id=test_id, status='Pending')
        db.session.add(o)
        db.session.commit()
        return o

    def _login(self, email):
        self.client.get('/auth/logout')
        r = self.client.get('/auth/login')
        return self.client.post('/auth/login', data={
            'email': email, 'password': '123456',
            'csrf_token': _csrf(r.data)}, follow_redirects=True)

    # ---- Unit: criticality evaluation --------------------------------
    def test_evaluate_criticality_bounds(self):
        from app.utils import evaluate_lab_criticality
        # below low threshold
        self.assertIs(evaluate_lab_criticality('0.3', 0.5, 50.0), True)
        # above high threshold
        self.assertIs(evaluate_lab_criticality('60', 0.5, 50.0), True)
        # within bounds
        self.assertIs(evaluate_lab_criticality('8', 0.5, 50.0), False)
        # single-sided (low only)
        self.assertIs(evaluate_lab_criticality('2', 3.0, None), True)
        self.assertIs(evaluate_lab_criticality('9', None, 14.0), False)
        # no thresholds configured
        self.assertIsNone(evaluate_lab_criticality('8', None, None))
        # non-numeric result cannot be evaluated
        self.assertIsNone(evaluate_lab_criticality('Positive', 0.5, 50.0))
        self.assertIsNone(evaluate_lab_criticality('', 0.5, 50.0))

    # ---- Route: entering a result crossing the threshold -------------
    def test_enter_result_auto_flags_critical_and_escalates(self):
        test = self._make_test(critical_low=0.5, critical_high=50.0,
                               critical_notes='Notify physician')
        order = self._make_order(test.id)
        self._login('lab@t.com')

        page = self.client.get(f'/lab/orders/{order.id}/result')
        self.assertEqual(page.status_code, 200)
        r = self.client.post(f'/lab/orders/{order.id}/result', data={
            'csrf_token': _csrf(page.data),
            'result_value': '60', 'result_unit': 'x10^9/L',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)

        res = LabResult.query.filter_by(order_id=order.id).first()
        self.assertIsNotNone(res)
        self.assertTrue(res.is_critical, 'value 60 must auto-flag critical')
        self.assertTrue(res.is_abnormal, '60 is also outside 4-11 normal range')
        order = db.session.get(LabOrder, order.id)
        self.assertEqual(order.status, 'Resulted')

        alert = ClinicalAlert.query.filter_by(
            patient_id=self.pat.id, alert_type='CRITICAL_LAB',
            source_type='lab_order', source_id=order.id, status='OPEN').first()
        self.assertIsNotNone(alert, 'a CRITICAL_LAB open alert must be raised')
        self.assertEqual(alert.severity, 'CRITICAL')

        n = Notification.query.filter_by(
            notification_type='critical', entity_type='lab_order',
            entity_id=order.id).first()
        self.assertIsNotNone(n, 'doctors must be notified of the panic value')

        # idempotency: re-saving the same critical value does not duplicate
        # the open alert.
        alerts = ClinicalAlert.query.filter_by(
            patient_id=self.pat.id, alert_type='CRITICAL_LAB',
            source_type='lab_order', source_id=order.id, status='OPEN').count()
        page2 = self.client.get(f'/lab/orders/{order.id}/result')
        self.client.post(f'/lab/orders/{order.id}/result', data={
            'csrf_token': _csrf(page2.data),
            'result_value': '62', 'result_unit': 'x10^9/L',
        }, follow_redirects=True)
        alerts2 = ClinicalAlert.query.filter_by(
            patient_id=self.pat.id, alert_type='CRITICAL_LAB',
            source_type='lab_order', source_id=order.id, status='OPEN').count()
        self.assertEqual(alerts2, alerts, 'escalation must not duplicate alerts')

    def test_enter_result_within_thresholds_is_not_critical(self):
        test = self._make_test(critical_low=0.5, critical_high=50.0)
        order = self._make_order(test.id)
        self._login('lab@t.com')
        page = self.client.get(f'/lab/orders/{order.id}/result')
        self.client.post(f'/lab/orders/{order.id}/result', data={
            'csrf_token': _csrf(page.data),
            'result_value': '8', 'result_unit': 'x10^9/L',
        }, follow_redirects=True)
        res = LabResult.query.filter_by(order_id=order.id).first()
        self.assertIsNotNone(res)
        self.assertFalse(res.is_critical, 'value within thresholds is not critical')
        alert_count = ClinicalAlert.query.filter_by(
            patient_id=self.pat.id, alert_type='CRITICAL_LAB').count()
        self.assertEqual(alert_count, 0)

    # ---- Verify: flags are re-derived at review time ------------------
    def test_verify_reflags_critical_added_after_entry(self):
        # Thresholds configured only AFTER the result was entered: verification
        # must re-derive the panic value and escalate.
        test = self._make_test()
        order = self._make_order(test.id)
        res = LabResult(order_id=order.id, result_value='60',
                        result_unit='x10^9/L', status='Draft', is_critical=False)
        db.session.add(res)
        db.session.commit()
        test.critical_high = 50.0
        db.session.commit()
        self._login('lab@t.com')

        page = self.client.get(f'/lab/orders/{order.id}/result')
        self.client.post(f'/lab/orders/{order.id}/verify',
                         data={'csrf_token': _csrf(page.data)},
                         follow_redirects=True)
        res = db.session.get(LabResult, res.id)
        self.assertEqual(res.status, 'Verified')
        self.assertTrue(res.is_critical,
                        'verification must re-derive critical flag from thresholds')
        alert = ClinicalAlert.query.filter_by(
            patient_id=self.pat.id, alert_type='CRITICAL_LAB',
            source_type='lab_order', source_id=order.id, status='OPEN').first()
        self.assertIsNotNone(alert)
        self.assertEqual(alert.severity, 'CRITICAL')

    # ---- Worklist: critical-only filter ------------------------------
    def test_worklist_critical_filter(self):
        test = self._make_test(critical_low=0.5, critical_high=50.0)
        o1 = LabOrder(patient_id=self.pat.id, doctor_id=self.doc_profile.id,
                      test_id=test.id, status='Verified')
        db.session.add(o1)
        db.session.flush()
        db.session.add(LabResult(order_id=o1.id, result_value='60', is_critical=True,
                                 status='Verified'))
        o2 = LabOrder(patient_id=self.pat.id, doctor_id=self.doc_profile.id,
                      test_id=test.id, status='Verified')
        db.session.add(o2)
        db.session.flush()
        db.session.add(LabResult(order_id=o2.id, result_value='8', is_critical=False,
                                 status='Verified'))
        db.session.commit()
        self._login('lab@t.com')

        r = self.client.get('/lab/orders?status=Verified&critical=1')
        self.assertEqual(r.status_code, 200)
        self.assertIn(str(o1.id).encode(), r.data)
        self.assertNotIn(f'>{o2.id}<'.encode(), r.data)

    # ---- Catalog: thresholds persist and render ----------------------
    def test_catalog_add_with_thresholds(self):
        self._login('adm@t.com')
        page = self.client.get('/lab/catalog/add')
        self.assertEqual(page.status_code, 200)
        r = self.client.post('/lab/catalog/add', data={
            'csrf_token': _csrf(page.data),
            'test_name': 'Serum Potassium',
            'category': 'Electrolytes',
            'normal_range': '3.5-5.3',
            'unit': 'mmol/L',
            'price': '30',
            'critical_low': '2.5',
            'critical_high': '6.5',
            'critical_notes': 'Cardiac arrest risk; call physician.',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        t = LabTestCatalog.query.filter_by(test_name='Serum Potassium').first()
        self.assertIsNotNone(t)
        self.assertEqual(t.critical_low, 2.5)
        self.assertEqual(t.critical_high, 6.5)
        self.assertEqual(t.critical_notes, 'Cardiac arrest risk; call physician.')
        # catalog page renders the critical range badge
        cat = self.client.get('/lab/catalog')
        self.assertIn(b'Serum Potassium', cat.data)
        self.assertIn(b'Critical Range', cat.data)


if __name__ == '__main__':
    unittest.main()