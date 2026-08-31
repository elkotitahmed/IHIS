"""Pharmacy workflow integration tests: dispense ledger, reject+notify, Rx tasks."""
import re
import unittest

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, Medication,
                        PharmacyInventory, Prescription, PrescriptionItem,
                        DispensingRecord, StockTransaction, Task, Notification,
                        Appointment, utcnow)


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class PharmacyWorkflowTestCase(unittest.TestCase):
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
        self.doc = self._make_user('doc@t.com', 'doctor', 'Doctor')
        self.doc_u = User.query.filter_by(email='doc@t.com').first()
        self.pharma = self._make_user('ph@t.com', 'pharm', 'Pharmacist')
        self.pharma_u = User.query.filter_by(email='ph@t.com').first()
        self.pat = User(username='pat', email='pat@t.com', full_name='Test P',
                        user_type='patient')
        self.pat.set_password('123456')
        self.pat.roles.append(Role.query.filter_by(name='Patient').first())
        db.session.add(self.pat)
        db.session.commit()
        self.patient = Patient(user_id=self.pat.id)
        db.session.add(self.patient)
        db.session.commit()
        self.patient_id = self.patient.id
        # Link doctor to patient via an appointment so need-to-know passes.
        doc_rec = Doctor.query.filter_by(user_id=self.doc_u.id).first()
        db.session.add(Appointment(patient_id=self.patient.id,
                                   doctor_id=doc_rec.id,
                                   scheduled_at=utcnow(),
                                   reason='Test'))
        db.session.commit()
        self.med = Medication(generic_name='Paracetamol', brand_name='Panadol')
        db.session.add(self.med)
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
            spec = Specialty(name='General')
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

    def _create_prescription(self):
        self._login(self.doc_u.email)
        page = self.client.get('/doctor/patients/1/prescriptions')
        tok = _csrf(page.data)
        self.client.post('/doctor/patients/1/prescriptions', data={
            'refills': '0',
            'medication_id': [str(self.med.id)],
            'dosage': ['500mg'], 'frequency': ['8h'], 'duration': ['5 days'],
            'instructions': ['after food'], 'quantity': ['10'],
            'csrf_token': tok,
        }, follow_redirects=True)
        return Prescription.query.first()

    def test_create_prescription_routes_pharmacy_task(self):
        rx = self._create_prescription()
        self.assertIsNotNone(rx)
        self.assertEqual(len(rx.items), 1)
        # rx task created for pharmacy
        task = Task.query.filter_by(related_resource_type='prescription',
                                    related_resource_id=rx.id).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.department, 'Pharmacy')
        # pharmacist notified
        n = Notification.query.filter_by(user_id=self.pharma_u.id,
                                          entity_type='prescription',
                                          entity_id=rx.id).first()
        self.assertIsNotNone(n)

    def test_dispense_records_stock_ledger_bill(self):
        rx = self._create_prescription()
        item = rx.items[0]
        inv = PharmacyInventory(medication_id=self.med.id, quantity=100,
                                reorder_level=10, expiry_date=None)
        db.session.add(inv)
        db.session.commit()
        self._login(self.pharma_u.email)
        page = self.client.get('/pharmacy/prescriptions')
        tok = _csrf(page.data)
        self.client.post(f'/pharmacy/prescriptions/{rx.id}/dispense', data={
            'item_id': item.id, 'quantity': '10', 'csrf_token': tok,
        }, follow_redirects=True)
        # stock decremented + ledger row
        inv = db.session.get(PharmacyInventory, inv.id)
        self.assertEqual(inv.quantity, 90)
        tx = StockTransaction.query.filter_by(medication_id=self.med.id).filter(
            StockTransaction.tx_type == 'DISPENSE').first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.quantity_change, -10)
        self.assertEqual(tx.quantity_after, 90)
        # dispensing record
        rec = DispensingRecord.query.filter_by(prescription_id=rx.id).first()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.quantity, 10)
        # item + rx dispensed
        item = db.session.get(PrescriptionItem, item.id)
        rx = db.session.get(Prescription, rx.id)
        self.assertEqual(item.status, 'Dispensed')
        self.assertEqual(rx.status, 'Dispensed')

    def test_reject_with_reason_notifies_doctor(self):
        rx = self._create_prescription()
        self._login(self.pharma_u.email)
        page = self.client.get('/pharmacy/prescriptions')
        tok = _csrf(page.data)
        self.client.post(f'/pharmacy/prescriptions/{rx.id}/reject', data={
            'reason': 'Drug interaction with current regimen', 'csrf_token': tok,
        }, follow_redirects=True)
        rx = db.session.get(Prescription, rx.id)
        self.assertEqual(rx.status, 'Cancelled')
        self.assertTrue(all(i.status == 'Cancelled' for i in rx.items))
        # doctor notified with critical flag
        n = Notification.query.filter_by(user_id=self.doc_u.id,
                                          entity_type='prescription',
                                          entity_id=rx.id).first()
        self.assertIsNotNone(n)
        self.assertIn('rejected', n.title.lower())


if __name__ == '__main__':
    unittest.main()
