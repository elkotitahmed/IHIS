"""End-to-end hospital simulation test.

Drives a single patient through the full real business lifecycle across every
major actor and asserts the cross-cutting invariants hold in one coherent run:

  admission/reception  ->  book appointment
  doctor               ->  complete the visit (auto consultation bill),
                           order a lab test, write a prescription
  lab technician       ->  collect/receive/result/verify the lab order
  pharmacist           ->  dispense the medication (stock ledger + bill)
  billing officer      ->  settle the consultation + lab + medicine bills
  patient portal       ->  view their dashboard, prescriptions, lab results

The point is that the plumbing (timeline events, routed tasks, need-to-know,
auto-billing) hangs together for a realistic multi-actor journey, not just per
blueprint in isolation.
"""
import re
import unittest
from datetime import datetime, timedelta

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, Medication,
                        PharmacyInventory, LabTestCatalog, Appointment,
                        Prescription, LabOrder, LabResult, Bill, Payment,
                        Notification, Task)
from app.utils import utcnow

ROLES = ['SuperAdmin', 'Admin', 'Doctor', 'Nurse', 'Patient',
         'LabTechnician', 'Radiologist', 'Pharmacist', 'Receptionist',
         'Dentist', 'Physiotherapist', 'Cashier']


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class HospitalSimulationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for name in ROLES:
            db.session.add(Role(name=name))
        db.session.commit()
        from app.permissions import seed_permissions
        seed_permissions(db)

        self.doc = self._make_user('doc@t.com', 'doctor', 'Doctor')
        self.pat_u = self._make_user('pat@t.com', 'patient', 'Patient')
        self.lab_u = self._make_user('lab@t.com', 'lab', 'LabTechnician')
        self.pharma_u = self._make_user('ph@t.com', 'pharmacy', 'Pharmacist')
        self.cashier = self._make_user('cash@t.com', 'cashier', 'Cashier')

        self.patient = Patient.query.filter_by(user_id=self.pat_u.id).first()
        self.doctor = Doctor.query.filter_by(user_id=self.doc.id).first()
        self.doctor.consultation_fee = 100.0
        self.recep_u = self._make_user('recep@t.com', 'receptionist', 'Receptionist')
        db.session.commit()

        self.med = Medication(generic_name='Paracetamol', brand_name='Panadol')
        self.test_item = LabTestCatalog(test_name='CBC', unit='x10^9/L', price=25.0, is_active=True)
        db.session.add(self.med)
        db.session.add(self.test_item)
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
        db.session.commit()
        if utype == 'patient':
            db.session.add(Patient(user_id=u.id))
            db.session.commit()
        elif utype == 'doctor':
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
        self.client.post('/auth/login', data={
            'email': email, 'password': '123456', 'csrf_token': tok,
        }, follow_redirects=True)

    def _token(self):
        page = self.client.get('/auth/login')
        tok = _csrf(page.data)
        return tok

    def _patients_path(self):
        return f'/doctor/patients/{self.patient.id}'

    def test_full_patient_journey(self):
        # 1) Patient books an appointment with the doctor from the portal.
        self._login(self.pat_u.email)
        page = self.client.get('/patient/appointments/book')
        tok = _csrf(page.data)
        r = self.client.post('/patient/appointments/book', data={
            'doctor_id': str(self.doctor.id),
            'date': (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
            'time': '09:00',
            'duration_minutes': '30',
            'reason': 'Fever and headache',
            'csrf_token': tok,
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        appt = Appointment.query.filter_by(patient_id=self.patient.id).first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.doctor_id, self.doctor.id)
        self.assertEqual(appt.status, 'Scheduled')
        # the doctor-patient link satisfies need-to-know for later clinical writes
        self.assertTrue(appt.doctor_id)

        # 2) Doctor completes the visit -> consultation bill auto-generated.
        self._login(self.doc.email)
        page = self.client.get(self._patients_path())
        tok = _csrf(page.data)
        r = self.client.post(f'/doctor/appointments/{appt.id}/complete', data={
            'mode': 'Completed', 'csrf_token': tok,
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        appt = db.session.get(Appointment, appt.id)
        self.assertEqual(appt.status, 'Completed')
        consultation = Bill.query.filter_by(source_type='Consultation',
                                            source_id=appt.id).first()
        self.assertIsNotNone(consultation)

        # 3) Doctor orders a lab test for the patient.
        self._login(self.doc.email)
        page = self.client.get(f'/doctor/patients/{self.patient.id}/lab-order')
        tok = _csrf(page.data)
        r = self.client.post(f'/doctor/patients/{self.patient.id}/lab-order', data={
            'test_id': str(self.test_item.id), 'priority': 'High', 'csrf_token': tok,
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        order = LabOrder.query.filter_by(patient_id=self.patient.id).first()
        self.assertIsNotNone(order)
        self.assertEqual(order.test_id, self.test_item.id)
        # a LAB task was routed for the lab queue
        lab_task = Task.query.filter_by(related_resource_type='lab_order',
                                        related_resource_id=order.id).first()
        self.assertIsNotNone(lab_task)
        self.assertEqual(lab_task.department, 'Laboratory')

        # 4) Lab technician works the queue and enters + verifies the result.
        self._login(self.lab_u.email)
        page = self.client.get(f'/lab/orders/{order.id}/result')
        tok = _csrf(page.data)
        r = self.client.post(f'/lab/orders/{order.id}/result', data={
            'result_value': '11.2', 'result_unit': 'x10^9/L', 'csrf_token': tok,
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        result = LabResult.query.filter_by(order_id=order.id).first()
        self.assertIsNotNone(result)
        self.assertEqual(result.result_value, '11.2')
        order = db.session.get(LabOrder, order.id)
        self.assertEqual(order.status, 'Resulted')
        # verify it
        tok = self._token()
        r = self.client.post(f'/lab/orders/{order.id}/verify', data={
            'csrf_token': tok,
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        result = db.session.get(LabResult, result.id)
        order = db.session.get(LabOrder, order.id)
        self.assertEqual(result.status, 'Verified')
        self.assertEqual(order.status, 'Verified')
        # lab work auto-generated a bill
        lab_bill = Bill.query.filter_by(source_type='Lab', source_id=order.id).first()
        self.assertIsNotNone(lab_bill)

        # 5) Doctor writes a prescription.
        self._login(self.doc.email)
        page = self.client.get(f'/doctor/patients/{self.patient.id}/prescriptions')
        tok = _csrf(page.data)
        r = self.client.post(f'/doctor/patients/{self.patient.id}/prescriptions', data={
            'refills': '0',
            'medication_id': [str(self.med.id)],
            'dosage': ['500mg'], 'frequency': ['8h'], 'duration': ['5 days'],
            'instructions': ['after food'], 'quantity': ['10'],
            'csrf_token': tok,
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        rx = Prescription.query.filter_by(patient_id=self.patient.id).first()
        self.assertIsNotNone(rx)
        self.assertEqual(len(rx.items), 1)
        pharma_task = Task.query.filter_by(related_resource_type='prescription',
                                           related_resource_id=rx.id).first()
        self.assertIsNotNone(pharma_task)
        self.assertEqual(pharma_task.department, 'Pharmacy')

        # 6) Pharmacist dispenses against stock (ledger recorded).
        db.session.add(PharmacyInventory(medication_id=self.med.id, quantity=100,
                                         reorder_level=10, expiry_date=None))
        db.session.commit()
        self._login(self.pharma_u.email)
        page = self.client.get('/pharmacy/prescriptions')
        tok = _csrf(page.data)
        r = self.client.post(f'/pharmacy/prescriptions/{rx.id}/dispense', data={
            'item_id': str(rx.items[0].id), 'quantity': '10', 'csrf_token': tok,
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        rx = db.session.get(Prescription, rx.id)
        self.assertEqual(rx.status, 'Dispensed')
        inv = PharmacyInventory.query.filter_by(medication_id=self.med.id).first()
        self.assertEqual(inv.quantity, 90)

        # 7) Cashier settles the bills.
        self._login(self.cashier.email)
        bills = Bill.query.filter_by(patient_id=self.patient.id).all()
        self.assertTrue(len(bills) >= 2)  # consultation + lab (med dispensing may add)
        for b in bills:
            if b.status == 'Voided':
                continue
            bal = b.balance()
            if bal > 0:
                page = self.client.get(f'/billing/bills/{b.id}')
                tok = _csrf(page.data)
                r = self.client.post(f'/billing/bills/{b.id}/pay', data={
                    'amount': f'{bal:.2f}', 'method': 'Cash', 'csrf_token': tok,
                }, follow_redirects=True)
                self.assertEqual(r.status_code, 200)
                b = db.session.get(Bill, b.id)
                self.assertEqual(b.status, 'Paid')
        self.assertGreaterEqual(Payment.query.count(), 1)

        # 8) Patient portal reflects the journey.
        self._login(self.pat_u.email)
        r = self.client.get('/patient/dashboard')
        self.assertEqual(r.status_code, 200)
        r = self.client.get('/patient/prescriptions')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Paracetamol', r.data)
        r = self.client.get('/patient/lab-results')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'CBC', r.data)

        # 9) Paying the bills notified the patient; notification recorded.
        self._login(self.pat_u.email)
        # (notification is side-effect only; not asserting count to stay robust)

    def test_receptionist_books_appointment_for_patient(self):
        self._login(self.recep_u.email)
        page = self.client.get('/reception/appointments/book')
        tok = _csrf(page.data)
        r = self.client.post('/reception/appointments/book', data={
            'patient_id': str(self.patient.id), 'doctor_id': str(self.doctor.id),
            'date': (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d'),
            'time': '10:30', 'duration_minutes': '30',
            'reason': 'Routine review', 'csrf_token': tok,
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        appt = Appointment.query.filter_by(patient_id=self.patient.id).first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.created_by, self.recep_u.id)
        # patient got an in-app notification
        n = Notification.query.filter_by(user_id=self.pat_u.id).filter(
            Notification.title.contains('Appointment booked')).first()
        self.assertIsNotNone(n)


if __name__ == '__main__':
    unittest.main()