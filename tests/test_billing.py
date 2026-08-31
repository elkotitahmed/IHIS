"""Billing subsystem tests: bills, line items, payments, auto-charging, and RBAC."""
import re
import unittest

from app import create_app, db
from app.models import (
    User, Role, Patient, Doctor, Specialty, Medication, Prescription,
    PrescriptionItem, LabTestCatalog, LabOrder, LabResult, ImagingType,
    RadiologyOrder, RadiologyReport, PharmacyInventory, Bill, BillItem, Payment,
    ServiceCatalog, Appointment,
)

ROLES = ['SuperAdmin', 'Admin', 'Doctor', 'Nurse', 'Patient',
         'LabTechnician', 'Radiologist', 'Pharmacist', 'Receptionist',
         'Dentist', 'Physiotherapist']


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class BillingTestCase(unittest.TestCase):
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
        self.admin = self._make_user('admin@test.com', 'admin', 'Admin')
        self.superadmin = self._make_user('root@test.com', 'admin', 'SuperAdmin')
        self.nurse = self._make_user('nurse@test.com', 'nurse', 'Nurse')
        self.pharma = self._make_user('pharma@test.com', 'pharmacist', 'Pharmacist')
        self.labtech = self._make_user('lab@test.com', 'lab_technician', 'LabTechnician')
        self.radio = self._make_user('radio@test.com', 'radiologist', 'Radiologist')
        self.reception = self._make_user('recep@test.com', 'receptionist', 'Receptionist')

        self.patient = Patient.query.filter_by(user_id=self.pat.id).first()
        self.doctor = Doctor.query.filter_by(user_id=self.doc.id).first()
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
            db.session.add(spec)
            db.session.commit()
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

    def test_auto_bill_on_lab_verify(self):
        t = LabTestCatalog(test_name='CBC', price=100.0)
        db.session.add(t); db.session.commit()
        o = LabOrder(patient_id=self.patient.id, doctor_id=self.doctor.id,
                     test_id=t.id, status='Pending')
        db.session.add(o); db.session.commit()
        db.session.add(LabResult(order_id=o.id, result_value='10.1', status='Draft'))
        db.session.commit()
        self._login(self.labtech.email)
        self.client.post(f'/lab/orders/{o.id}/verify',
                         data={'csrf_token': _csrf(self.client.get(f'/lab/orders/{o.id}/result').data)},
                         follow_redirects=True)
        bill = Bill.query.filter_by(source_type='Lab', source_id=o.id).first()
        self.assertIsNotNone(bill)
        self.assertAlmostEqual(bill.total(), 100.0)
        self.assertEqual(bill.items[0].description, 'Laboratory — CBC')

    def test_auto_bill_on_radiology_sign(self):
        it = ImagingType(name='X-Ray', price=120.0)
        db.session.add(it); db.session.commit()
        o = RadiologyOrder(patient_id=self.patient.id, doctor_id=self.doctor.id,
                           imaging_type_id=it.id, status='Completed')
        db.session.add(o); db.session.commit()
        db.session.add(RadiologyReport(order_id=o.id, findings='Clear', status='Draft'))
        db.session.commit()
        self._login(self.radio.email)
        self.client.post(f'/radiology/orders/{o.id}/sign',
                         data={'csrf_token': _csrf(self.client.get(f'/radiology/orders/{o.id}/report').data)},
                         follow_redirects=True)
        bill = Bill.query.filter_by(source_type='Radiology', source_id=o.id).first()
        self.assertIsNotNone(bill)
        self.assertAlmostEqual(bill.total(), 120.0)

    def test_auto_bill_on_dispense(self):
        med = Medication(generic_name='Metformin'); db.session.add(med); db.session.commit()
        db.session.add(PharmacyInventory(medication_id=med.id, quantity=50,
                                         selling_price=15.0))
        rx = Prescription(patient_id=self.patient.id, doctor_id=self.doctor.id,
                          status='Active')
        db.session.add(rx); db.session.commit()
        db.session.add(PrescriptionItem(prescription_id=rx.id, medication_id=med.id,
                                        quantity=2))
        db.session.commit()
        self._login(self.pharma.email)
        p = self.client.get('/pharmacy/prescriptions')
        self.client.post(f'/pharmacy/prescriptions/{rx.id}/dispense',
                         data={'csrf_token': _csrf(p.data)}, follow_redirects=True)
        bill = Bill.query.filter_by(source_type='Pharmacy', source_id=rx.id).first()
        self.assertIsNotNone(bill)
        self.assertAlmostEqual(bill.total(), 30.0)

    def test_manual_bill_and_payment_partial_then_full(self):
        self._login(self.reception.email)
        page = self.client.get('/billing/bills/new')
        self.assertEqual(page.status_code, 200)
        r = self.client.post('/billing/bills/new', data={
            'csrf_token': _csrf(page.data),
            'patient_id': str(self.patient.id),
            'discount': '0', 'tax_percent': '0', 'notes': 'test',
            'desc': ['Consultation', 'Room'],
            'qty': ['1', '1'],
            'price': ['100', '200'],
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        bill = Bill.query.filter_by(patient_id=self.patient.id,
                                    source_type='Manual').first()
        self.assertIsNotNone(bill)
        self.assertAlmostEqual(bill.total(), 300.0)
        self.assertEqual(bill.status, 'Unpaid')

        # partial payment
        page = self.client.get(f'/billing/bills/{bill.id}')
        self.client.post(f'/billing/bills/{bill.id}/pay', data={
            'csrf_token': _csrf(page.data), 'amount': '100', 'method': 'Cash',
        }, follow_redirects=True)
        db.session.refresh(bill)
        self.assertEqual(bill.status, 'PartiallyPaid')
        self.assertAlmostEqual(bill.balance(), 200.0)
        self.assertEqual(len(bill.payments), 1)

        # full settlement
        page = self.client.get(f'/billing/bills/{bill.id}')
        self.client.post(f'/billing/bills/{bill.id}/pay', data={
            'csrf_token': _csrf(page.data), 'amount': '200', 'method': 'Card',
            'reference': 'CARD-1',
        }, follow_redirects=True)
        db.session.refresh(bill)
        self.assertEqual(bill.status, 'Paid')
        self.assertAlmostEqual(bill.balance(), 0.0)

    def test_overflow_payment_blocked(self):
        self._login(self.reception.email)
        page = self.client.get('/billing/bills/new')
        self.client.post('/billing/bills/new', data={
            'csrf_token': _csrf(page.data), 'patient_id': str(self.patient.id),
            'discount': '0', 'tax_percent': '0', 'notes': '',
            'desc': ['A'], 'qty': ['1'], 'price': ['50'],
        }, follow_redirects=True)
        bill = Bill.query.filter_by(source_type='Manual').first()
        page = self.client.get(f'/billing/bills/{bill.id}')
        self.client.post(f'/billing/bills/{bill.id}/pay', data={
            'csrf_token': _csrf(page.data), 'amount': '999', 'method': 'Cash',
        }, follow_redirects=True)
        db.session.refresh(bill)
        self.assertEqual(len(bill.payments), 0)

    def test_billing_permission_gating(self):
        # Nurse lacks all billing permissions -> 403 on billing routes
        self._login(self.nurse.email)
        self.assertEqual(self.client.get('/billing/bills').status_code, 403)
        self.assertEqual(self.client.get('/billing/dashboard').status_code, 403)

    def test_void_bill(self):
        self._login(self.reception.email)
        page = self.client.get('/billing/bills/new')
        self.client.post('/billing/bills/new', data={
            'csrf_token': _csrf(page.data), 'patient_id': str(self.patient.id),
            'discount': '0', 'tax_percent': '0', 'notes': '',
            'desc': ['A'], 'qty': ['1'], 'price': ['50'],
        }, follow_redirects=True)
        bill = Bill.query.filter_by(source_type='Manual').first()
        # Receptionist has no BILL_VOID permission -> blocked
        page = self.client.get(f'/billing/bills/{bill.id}')
        self.client.post(f'/billing/bills/{bill.id}/void',
                         data={'csrf_token': _csrf(page.data), 'reason': 'wrong'},
                         follow_redirects=True)
        db.session.refresh(bill)
        self.assertEqual(bill.status, 'Unpaid')
        # Admin (has BILL_VOID) can void
        self._logout()
        self._login(self.admin.email)
        page = self.client.get(f'/billing/bills/{bill.id}')
        self.client.post(f'/billing/bills/{bill.id}/void',
                         data={'csrf_token': _csrf(page.data), 'reason': 'wrong entry'},
                         follow_redirects=True)
        db.session.refresh(bill)
        self.assertEqual(bill.status, 'Voided')


    def test_consultation_bill_on_appointment_complete(self):
        from datetime import datetime, timedelta
        self.doctor.consultation_fee = 200.0
        db.session.commit()
        appt = Appointment(patient_id=self.patient.id, doctor_id=self.doctor.id,
                           scheduled_at=datetime.now() + timedelta(hours=1),
                           status='CheckedIn')
        db.session.add(appt); db.session.commit()

        self._login(self.doc.email)
        page = self.client.get('/doctor/appointments')
        self.client.post(f'/doctor/appointments/{appt.id}/complete', data={
            'csrf_token': _csrf(page.data), 'mode': 'Completed',
        }, follow_redirects=True)

        bill = Bill.query.filter_by(source_type='Consultation', source_id=appt.id).first()
        self.assertIsNotNone(bill)
        self.assertAlmostEqual(bill.total(), 200.0)
        db.session.refresh(appt)
        self.assertEqual(appt.status, 'Completed')

        # completing again does not duplicate the consultation bill
        page = self.client.get('/doctor/appointments')
        self.client.post(f'/doctor/appointments/{appt.id}/complete', data={
            'csrf_token': _csrf(page.data), 'mode': 'Completed',
        }, follow_redirects=True)
        count = Bill.query.filter_by(source_type='Consultation', source_id=appt.id).count()
        self.assertEqual(count, 1)

    def test_consultation_bill_no_show_not_generated(self):
        from datetime import datetime, timedelta
        self.doctor.consultation_fee = 150.0
        db.session.commit()
        appt = Appointment(patient_id=self.patient.id, doctor_id=self.doctor.id,
                           scheduled_at=datetime.now() + timedelta(hours=1),
                           status='CheckedIn')
        db.session.add(appt); db.session.commit()
        self._login(self.doc.email)
        page = self.client.get('/doctor/appointments')
        self.client.post(f'/doctor/appointments/{appt.id}/complete', data={
            'csrf_token': _csrf(page.data), 'mode': 'NoShow',
        }, follow_redirects=True)
        db.session.refresh(appt)
        self.assertEqual(appt.status, 'NoShow')
        self.assertIsNone(Bill.query.filter_by(source_type='Consultation',
                                               source_id=appt.id).first())

    def test_patient_bill_view(self):
        bill = Bill(patient_id=self.patient.id, bill_no='INV-P1', status='Unpaid',
                    source_type='Manual')
        db.session.add(bill); db.session.flush()
        db.session.add(BillItem(bill_id=bill.id, description='Consultation',
                                quantity=1, unit_price=100.0))
        db.session.commit()
        self._login(self.pat.email)
        r = self.client.get('/patient/bills')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'INV-P1', r.data)


    def test_reception_register_patient(self):
        self._login(self.reception.email)
        page = self.client.get('/reception/register')
        self.assertEqual(page.status_code, 200)
        r = self.client.post('/reception/register', data={
            'csrf_token': _csrf(page.data),
            'full_name': 'Newly Registered',
            'username': 'newpat',
            'password': 'secret1',
            'email': 'newpat@test.com',
            'phone': '0555',
            'gender': 'Female',
            'date_of_birth': '1990-05-10',
            'blood_type': 'O+',
            'address': '123 Main St',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        user = User.query.filter_by(username='newpat').first()
        self.assertIsNotNone(user)
        self.assertTrue(any(role.name == 'Patient' for role in user.roles))
        pat = Patient.query.filter_by(user_id=user.id).first()
        self.assertIsNotNone(pat)
        self.assertEqual(pat.gender, 'Female')
        self.assertEqual(pat.blood_type, 'O+')
        self.assertEqual(pat.user.full_name, 'Newly Registered')
        # duplicate username rejected
        r2 = self.client.post('/reception/register', data={
            'csrf_token': _csrf(self.client.get('/reception/register').data),
            'full_name': 'Dup', 'username': 'newpat', 'password': 'x'},
            follow_redirects=True)
        self.assertIn(b'already exists', r2.data)


    def test_notification_created_on_lab_verify(self):
        from app.models import Notification
        t = LabTestCatalog(test_name='CBC', price=50.0)
        db.session.add(t); db.session.commit()
        o = LabOrder(patient_id=self.patient.id, doctor_id=self.doctor.id,
                     test_id=t.id, status='Pending')
        db.session.add(o); db.session.commit()
        db.session.add(LabResult(order_id=o.id, result_value='10.1', status='Draft'))
        db.session.commit()
        self._login(self.labtech.email)
        self.client.post(f'/lab/orders/{o.id}/verify',
                         data={'csrf_token': _csrf(self.client.get(f'/lab/orders/{o.id}/result').data)},
                         follow_redirects=True)
        notif = Notification.query.filter_by(
            user_id=self.patient.user_id, title='Lab result ready').first()
        self.assertIsNotNone(notif)


if __name__ == '__main__':
    unittest.main()
