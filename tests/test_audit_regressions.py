"""Regression tests for the full-system audit repairs.

Each test pins a behaviour that was broken or missing before the audit:
need-to-know scoping for non-doctor roles, locked lab results, real re-orders,
expired stock, task auto-completion, notification de-duplication, referral
lifecycle, MRN assignment, numbering, appointment ownership, the supervisor
patient-portal preview, the admission detail page and the radiology
technician / radiologist separation of duties.
"""
import unittest
from datetime import date, datetime, timedelta

from app import create_app, db
from app.models import (
    Admission, Appointment, Bed, Bill, Doctor, LabOrder, LabResult,
    LabTestCatalog, Medication, MedicationAdministration, Notification,
    Patient, PharmacyInventory, Prescription, PrescriptionItem, Referral,
    Role, Specialty, Task, User, Ward, ImagingType, RadiologyOrder,
    RadiologyReport,
)
from app.permissions import seed_permissions
from seed import ROLES


class AuditBase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.roles = {}
        for name in ROLES:
            r = Role(name=name)
            db.session.add(r)
            self.roles[name] = r
        db.session.commit()
        seed_permissions(db)
        self.spec = Specialty(name='General')
        db.session.add(self.spec)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ---- helpers -----------------------------------------------------
    def user(self, name, utype, role, department_id=None):
        u = User(username=name, email=f'{name}@t.com', full_name=f'Test {name}',
                 user_type=utype, department_id=department_id)
        u.set_password('123456')
        u.roles.append(self.roles[role])
        db.session.add(u)
        db.session.flush()
        if role == 'Doctor':
            db.session.add(Doctor(user_id=u.id, specialty_id=self.spec.id,
                                  consultation_fee=100.0))
        db.session.commit()
        return u

    def patient(self, name='pat'):
        u = User(username=name, email=f'{name}@t.com', full_name=f'Patient {name}',
                 user_type='patient')
        u.set_password('123456')
        u.roles.append(self.roles['Patient'])
        db.session.add(u)
        db.session.flush()
        p = Patient(user_id=u.id)
        db.session.add(p)
        db.session.commit()
        return p

    def login(self, email):
        # The login page redirects an already-authenticated session, so end
        # the previous role's session first.
        self.client.get('/auth/logout')
        r = self.client.post('/auth/login', data={'email': email, 'password': '123456'})
        self.assertIn(r.status_code, (302, 200))
        return r

    def lab_test(self):
        t = LabTestCatalog(test_name='CBC', category='Hematology',
                           normal_range='4-11', unit='x', price=50.0)
        db.session.add(t)
        db.session.commit()
        return t


class NeedToKnowScopingTests(AuditBase):
    def test_nurse_sees_only_admitted_or_today_patients(self):
        nurse = self.user('nurse', 'nurse', 'Nurse')
        p_in = self.patient('inpatient')
        p_other = self.patient('other')
        ward = Ward(name='W1')
        db.session.add(ward)
        db.session.flush()
        bed = Bed(ward_id=ward.id, bed_no='B01', status='Occupied')
        db.session.add(bed)
        db.session.flush()
        db.session.add(Admission(admission_no='ADM-1', patient_id=p_in.id, ward_id=ward.id,
                                 bed_id=bed.id, status='Admitted'))
        db.session.commit()
        self.login(nurse.email)
        self.assertEqual(self.client.get(f'/nursing/vitals'.replace('vitals', f'patients/{p_in.id}/vitals')).status_code, 200)
        self.assertEqual(self.client.get(f'/nursing/patients/{p_other.id}/vitals').status_code, 403)
        body = self.client.get('/nursing/patients').get_data(as_text=True)
        self.assertIn('Patient inpatient', body)
        self.assertNotIn('Patient other', body)

    def test_physiotherapist_gains_access_through_referral(self):
        physio = self.user('physio', 'physiotherapist', 'Physiotherapist')
        doc = self.user('doc', 'doctor', 'Doctor')
        p = self.patient()
        self.login(physio.email)
        self.assertEqual(self.client.get(f'/physiotherapy/patients/{p.id}/assessment').status_code, 403)
        db.session.add(Referral(patient_id=p.id, from_doctor_id=doc.doctor_profile.id,
                                to_specialty='Physiotherapy', reason='knee', status='SENT'))
        db.session.commit()
        self.assertEqual(self.client.get(f'/physiotherapy/patients/{p.id}/assessment').status_code, 200)
        # Referral worklist shows it to the physiotherapist
        body = self.client.get('/care/referrals').get_data(as_text=True)
        self.assertIn('knee', body)

    def test_department_scoping_opens_patient_to_department_staff(self):
        from app.models import Department
        dept = Department(name='Dermatology')
        db.session.add(dept)
        db.session.commit()
        doc = self.user('derm', 'doctor', 'Doctor', department_id=dept.id)
        p = self.patient()
        p.department_id = dept.id
        db.session.commit()
        self.login(doc.email)
        self.assertEqual(self.client.get(f'/doctor/patients/{p.id}').status_code, 200)


class LabIntegrityTests(AuditBase):
    def _order_with_result(self, status='Finalized'):
        doc = self.user('doc', 'doctor', 'Doctor')
        p = self.patient()
        t = self.lab_test()
        o = LabOrder(patient_id=p.id, doctor_id=doc.doctor_profile.id, test_id=t.id,
                     status=status)
        db.session.add(o)
        db.session.flush()
        res = LabResult(order_id=o.id, result_value='5', status=status)
        db.session.add(res)
        db.session.commit()
        return o, res

    def test_finalized_result_is_locked_against_direct_edit(self):
        tech = self.user('tech', 'lab_technician', 'LabTechnician')
        o, res = self._order_with_result('Finalized')
        self.login(tech.email)
        r = self.client.post(f'/lab/orders/{o.id}/result', data={'result_value': '99'},
                             follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(LabResult.query.get(res.id).result_value, '5')
        self.assertIn('locked', r.get_data(as_text=True).lower())

    def test_reorder_creates_new_accessioned_order_and_cancels_task(self):
        tech = self.user('tech', 'lab_technician', 'LabTechnician')
        doc = self.user('doc', 'doctor', 'Doctor')
        p = self.patient()
        t = self.lab_test()
        o = LabOrder(patient_id=p.id, doctor_id=doc.doctor_profile.id, test_id=t.id,
                     status='Rejected', rejection_reason='haemolysed')
        db.session.add(o)
        db.session.flush()
        db.session.add(Task(title='lab', task_type='LAB', department='Laboratory',
                            related_resource_type='lab_order', related_resource_id=o.id,
                            status='NEW'))
        db.session.commit()
        self.login(tech.email)
        self.client.post(f'/lab/orders/{o.id}/reorder', follow_redirects=True)
        orders = LabOrder.query.order_by(LabOrder.id).all()
        self.assertEqual(len(orders), 2)
        new = orders[-1]
        self.assertEqual(new.reordered_from, o.id)
        self.assertEqual(new.status, 'Pending')
        self.assertTrue(new.accession_number.startswith('LAB-'))
        old_task = Task.query.filter_by(related_resource_id=o.id).first()
        self.assertEqual(old_task.status, 'CANCELLED')
        self.assertIsNotNone(Task.query.filter_by(related_resource_id=new.id).first())

    def test_verify_completes_lab_task_and_notifies_ordering_doctor_only(self):
        tech = self.user('tech', 'lab_technician', 'LabTechnician')
        doc = self.user('doc', 'doctor', 'Doctor')
        other_doc = self.user('other', 'doctor', 'Doctor')
        p = self.patient()
        t = self.lab_test()
        o = LabOrder(patient_id=p.id, doctor_id=doc.doctor_profile.id, test_id=t.id,
                     status='Resulted')
        db.session.add(o)
        db.session.flush()
        db.session.add(LabResult(order_id=o.id, result_value='5', status='Draft'))
        db.session.add(Task(title='lab', task_type='LAB', department='Laboratory',
                            related_resource_type='lab_order', related_resource_id=o.id,
                            status='NEW'))
        db.session.commit()
        self.login(tech.email)
        self.client.post(f'/lab/orders/{o.id}/verify', follow_redirects=True)
        self.assertEqual(Task.query.filter_by(related_resource_id=o.id).first().status, 'COMPLETED')
        self.assertTrue(Notification.query.filter_by(user_id=doc.id, entity_type='lab_order').count() >= 1)
        self.assertEqual(Notification.query.filter_by(user_id=other_doc.id).count(), 0)


class NotificationDedupeTests(AuditBase):
    def test_same_unread_notification_is_not_duplicated(self):
        from app.services.notifications import notify
        u = self.user('n', 'nurse', 'Nurse')
        notify(u.id, 'Result ready', 'a', entity_type='lab_order', entity_id=1)
        notify(u.id, 'Result ready', 'b', entity_type='lab_order', entity_id=1)
        db.session.commit()
        self.assertEqual(Notification.query.filter_by(user_id=u.id).count(), 1)
        Notification.query.first().is_read = True
        db.session.commit()
        notify(u.id, 'Result ready', 'c', entity_type='lab_order', entity_id=1)
        db.session.commit()
        self.assertEqual(Notification.query.filter_by(user_id=u.id).count(), 2)


class PharmacyTests(AuditBase):
    def test_expired_batches_are_never_dispensed(self):
        pharm = self.user('ph', 'pharmacist', 'Pharmacist')
        doc = self.user('doc', 'doctor', 'Doctor')
        p = self.patient()
        med = Medication(generic_name='Amoxicillin')
        db.session.add(med)
        db.session.flush()
        db.session.add(PharmacyInventory(medication_id=med.id, quantity=100,
                                         expiry_date=date.today() - timedelta(days=1)))
        rx = Prescription(patient_id=p.id, doctor_id=doc.doctor_profile.id, status='Active')
        db.session.add(rx)
        db.session.flush()
        item = PrescriptionItem(prescription_id=rx.id, medication_id=med.id, quantity=5)
        db.session.add(item)
        db.session.commit()
        self.login(pharm.email)
        r = self.client.post(f'/pharmacy/prescriptions/{rx.id}/dispense',
                             data={'item_id': item.id, 'quantity': 5}, follow_redirects=True)
        self.assertIn('expired', r.get_data(as_text=True).lower())
        self.assertEqual(PharmacyInventory.query.first().quantity, 100)
        self.assertEqual(PrescriptionItem.query.get(item.id).status, 'Active')

    def test_dispensing_completes_pharmacy_task(self):
        pharm = self.user('ph', 'pharmacist', 'Pharmacist')
        doc = self.user('doc', 'doctor', 'Doctor')
        p = self.patient()
        med = Medication(generic_name='Metformin')
        db.session.add(med)
        db.session.flush()
        db.session.add(PharmacyInventory(medication_id=med.id, quantity=100, selling_price=1.0,
                                         expiry_date=date.today() + timedelta(days=365)))
        rx = Prescription(patient_id=p.id, doctor_id=doc.doctor_profile.id, status='Active')
        db.session.add(rx)
        db.session.flush()
        item = PrescriptionItem(prescription_id=rx.id, medication_id=med.id, quantity=5)
        db.session.add(item)
        db.session.add(Task(title='disp', task_type='PHARMACY', department='Pharmacy',
                            related_resource_type='prescription', related_resource_id=rx.id,
                            status='NEW'))
        db.session.commit()
        self.login(pharm.email)
        self.client.post(f'/pharmacy/prescriptions/{rx.id}/dispense',
                         data={'item_id': item.id, 'quantity': 5}, follow_redirects=True)
        self.assertEqual(Prescription.query.get(rx.id).status, 'Dispensed')
        self.assertEqual(Task.query.filter_by(related_resource_id=rx.id).first().status, 'COMPLETED')
        bill = Bill.query.filter_by(source_type='Pharmacy', source_id=rx.id).first()
        self.assertIsNotNone(bill)
        self.assertTrue(bill.bill_no.startswith('INV-'))


class ReferralLifecycleTests(AuditBase):
    def test_receiver_accepts_then_completes_and_referrer_is_notified(self):
        sender = self.user('sender', 'doctor', 'Doctor')
        receiver = self.user('receiver', 'doctor', 'Doctor')
        p = self.patient()
        db.session.add(Appointment(patient_id=p.id, doctor_id=sender.doctor_profile.id,
                                   scheduled_at=datetime.now()))
        db.session.commit()
        self.login(sender.email)
        r = self.client.post('/care/referrals/new', data={
            'patient_id': p.id, 'to_doctor_id': receiver.doctor_profile.id,
            'reason': 'cardiology review', 'urgency': 'Urgent'}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        ref = Referral.query.first()
        self.assertEqual(ref.status, 'SENT')
        task = Task.query.filter_by(related_resource_type='referral', related_resource_id=ref.id).first()
        self.assertEqual(task.assigned_to, receiver.id)
        # The sender may not accept their own referral.
        r = self.client.post(f'/care/referrals/{ref.id}/status', data={'status': 'ACCEPTED'})
        self.assertEqual(r.status_code, 403)
        self.login(receiver.email)
        self.client.post(f'/care/referrals/{ref.id}/status', data={'status': 'ACCEPTED'})
        self.assertEqual(Referral.query.get(ref.id).status, 'ACCEPTED')
        self.client.post(f'/care/referrals/{ref.id}/status',
                         data={'status': 'COMPLETED', 'response': 'Seen; echo normal.'})
        ref = Referral.query.get(ref.id)
        self.assertEqual(ref.status, 'COMPLETED')
        self.assertEqual(ref.response, 'Seen; echo normal.')
        self.assertEqual(Task.query.get(task.id).status, 'COMPLETED')
        self.assertTrue(Notification.query.filter_by(user_id=sender.id, entity_type='referral').count() >= 1)
        # Illegal transition is refused.
        r = self.client.post(f'/care/referrals/{ref.id}/status', data={'status': 'ACCEPTED'},
                             follow_redirects=True)
        self.assertEqual(Referral.query.get(ref.id).status, 'COMPLETED')


class ReceptionTests(AuditBase):
    def test_registration_assigns_mrn_and_booking_validates_ids(self):
        rec = self.user('rec', 'receptionist', 'Receptionist')
        self.login(rec.email)
        self.client.post('/reception/register', data={
            'full_name': 'New Person', 'username': 'newp', 'password': 'secret1',
            'email': 'newp@t.com'}, follow_redirects=True)
        p = Patient.query.join(User).filter(User.username == 'newp').first()
        self.assertIsNotNone(p)
        self.assertEqual(p.mrn, f'MRN-{p.id:06d}')
        r = self.client.post('/reception/appointments/book', data={
            'patient_id': 9999, 'doctor_id': 9999, 'date': '2030-01-01', 'time': '10:00'},
            follow_redirects=True)
        self.assertIn('valid patient and doctor', r.get_data(as_text=True))
        self.assertEqual(Appointment.query.count(), 0)

    def test_checkin_assigns_queue_number_and_cancel_reschedule(self):
        rec = self.user('rec', 'receptionist', 'Receptionist')
        doc = self.user('doc', 'doctor', 'Doctor')
        p = self.patient()
        a = Appointment(patient_id=p.id, doctor_id=doc.doctor_profile.id,
                        scheduled_at=datetime.now() + timedelta(hours=1), status='Scheduled')
        b = Appointment(patient_id=p.id, doctor_id=doc.doctor_profile.id,
                        scheduled_at=datetime.now() + timedelta(days=2), status='Scheduled')
        db.session.add_all([a, b])
        db.session.commit()
        self.login(rec.email)
        self.client.post(f'/reception/appointments/{a.id}/checkin', follow_redirects=True)
        a = Appointment.query.get(a.id)
        self.assertEqual(a.status, 'CheckedIn')
        self.assertEqual(a.queue_number, 1)
        self.assertIsNotNone(a.checked_in_at)
        # cannot check in twice
        r = self.client.post(f'/reception/appointments/{a.id}/checkin', follow_redirects=True)
        self.assertIn('Cannot check in', r.get_data(as_text=True))
        self.client.post(f'/reception/appointments/{b.id}/reschedule',
                         data={'date': '2030-05-05', 'time': '09:30'}, follow_redirects=True)
        self.assertEqual(Appointment.query.get(b.id).scheduled_at, datetime(2030, 5, 5, 9, 30))
        self.client.post(f'/reception/appointments/{b.id}/cancel', data={'reason': 'patient request'},
                         follow_redirects=True)
        self.assertEqual(Appointment.query.get(b.id).status, 'Cancelled')


class DoctorOwnershipTests(AuditBase):
    def test_doctor_cannot_complete_another_doctors_appointment(self):
        d1 = self.user('d1', 'doctor', 'Doctor')
        d2 = self.user('d2', 'doctor', 'Doctor')
        p = self.patient()
        a = Appointment(patient_id=p.id, doctor_id=d1.doctor_profile.id,
                        scheduled_at=datetime.now(), status='CheckedIn')
        db.session.add(a)
        db.session.commit()
        self.login(d2.email)
        self.assertEqual(self.client.post(f'/doctor/appointments/{a.id}/complete').status_code, 403)
        self.assertEqual(self.client.post(f'/doctor/appointments/{a.id}/start').status_code, 403)
        self.login(d1.email)
        self.client.post(f'/doctor/appointments/{a.id}/start')
        self.assertEqual(Appointment.query.get(a.id).status, 'InConsultation')
        self.client.post(f'/doctor/appointments/{a.id}/complete', data={'mode': 'Completed'})
        self.assertEqual(Appointment.query.get(a.id).status, 'Completed')
        self.assertIsNotNone(Bill.query.filter_by(source_type='Consultation', source_id=a.id).first())

    def test_cancelling_prescription_discontinues_mar_doses(self):
        d1 = self.user('d1', 'doctor', 'Doctor')
        p = self.patient()
        med = Medication(generic_name='X')
        db.session.add(med)
        db.session.flush()
        rx = Prescription(patient_id=p.id, doctor_id=d1.doctor_profile.id, status='Active')
        db.session.add(rx)
        db.session.flush()
        item = PrescriptionItem(prescription_id=rx.id, medication_id=med.id)
        db.session.add(item)
        db.session.flush()
        db.session.add(MedicationAdministration(patient_id=p.id, prescription_id=rx.id,
                                                prescription_item_id=item.id, status='Scheduled'))
        db.session.commit()
        self.login(d1.email)
        self.client.post(f'/doctor/prescriptions/{rx.id}/cancel', follow_redirects=True)
        self.assertEqual(MedicationAdministration.query.first().status, 'Discontinued')


class PortalAndAdmissionTests(AuditBase):
    def test_superadmin_previews_patient_portal_without_500(self):
        sa = self.user('sa', 'admin', 'SuperAdmin')
        p = self.patient()
        self.login(sa.email)
        for path in ('/patient/dashboard', '/patient/medical-history', '/patient/appointments',
                     '/patient/prescriptions', '/patient/lab-results', '/patient/bills',
                     '/patient/documents', '/patient/radiology-reports', '/patient/my-radiology'):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
        r = self.client.get(f'/patient/preview/{p.id}', follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('read-only preview', r.get_data(as_text=True))
        # Supervisors cannot edit the patient's profile from the preview.
        self.client.post('/patient/profile', data={'full_name': 'Hacked'}, follow_redirects=True)
        self.assertNotEqual(User.query.get(p.user_id).full_name, 'Hacked')

    def test_admission_detail_page_and_doctor_discharge(self):
        rec = self.user('rec', 'receptionist', 'Receptionist')
        doc = self.user('doc', 'doctor', 'Doctor')
        p = self.patient()
        ward = Ward(name='W1', room_charge_per_day=100.0)
        db.session.add(ward)
        db.session.flush()
        bed = Bed(ward_id=ward.id, bed_no='B01')
        db.session.add(bed)
        db.session.commit()
        self.login(rec.email)
        self.client.post('/admissions/admit', data={
            'patient_id': p.id, 'ward_id': ward.id, 'bed_id': bed.id,
            'doctor_id': doc.doctor_profile.id, 'reason': 'pneumonia'}, follow_redirects=True)
        adm = Admission.query.first()
        self.assertEqual(adm.admission_no, f'ADM-{1000 + adm.id}')
        self.assertEqual(Bed.query.get(bed.id).status, 'Occupied')
        self.assertIsNotNone(Task.query.filter_by(related_resource_type='admission').first())
        # second admission into the same bed is refused
        p2 = self.patient('p2')
        r = self.client.post('/admissions/admit', data={
            'patient_id': p2.id, 'ward_id': ward.id, 'bed_id': bed.id}, follow_redirects=True)
        self.assertIn('not available', r.get_data(as_text=True))
        self.login(doc.email)
        self.assertEqual(self.client.get(f'/admissions/admissions/{adm.id}').status_code, 200)
        r = self.client.post(f'/admissions/admissions/{adm.id}/discharge', data={
            'discharge_diagnosis': 'Resolved pneumonia', 'discharge_summary': 'ok',
            'follow_up_days': 7}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        adm = Admission.query.get(adm.id)
        self.assertEqual(adm.status, 'Discharged')
        self.assertEqual(Bed.query.get(bed.id).status, 'Available')
        room_bill = Bill.query.filter_by(source_type='Room', source_id=adm.id).first()
        self.assertIsNotNone(room_bill)
        self.assertTrue(room_bill.bill_no.startswith('INV-'))
        from app.models import FollowUp
        self.assertEqual(FollowUp.query.count(), 1)


class RadiologySeparationTests(AuditBase):
    def _order(self, doc):
        p = self.patient()
        it = ImagingType(name='X-Ray', price=100.0)
        db.session.add(it)
        db.session.flush()
        o = RadiologyOrder(patient_id=p.id, doctor_id=doc.doctor_profile.id,
                           imaging_type_id=it.id, status='Arrived')
        db.session.add(o)
        db.session.commit()
        return o

    def test_technician_performs_but_cannot_report_or_sign(self):
        doc = self.user('doc', 'doctor', 'Doctor')
        tech = self.user('tech', 'radiology_technician', 'RadiologyTechnician')
        rad = self.user('rad', 'radiologist', 'Radiologist')
        o = self._order(doc)
        self.login(tech.email)
        self.client.post(f'/radiology/orders/{o.id}/perform', follow_redirects=True)
        self.assertEqual(RadiologyOrder.query.get(o.id).status, 'Performed')
        self.assertIsNotNone(Task.query.filter_by(related_resource_type='radiology_report_task').first())
        self.assertEqual(self.client.get(f'/radiology/orders/{o.id}/report').status_code, 403)
        self.assertEqual(self.client.post(f'/radiology/orders/{o.id}/sign').status_code, 403)
        self.login(rad.email)
        self.client.post(f'/radiology/orders/{o.id}/report', data={
            'findings': 'f', 'impression': 'Large pneumothorax', 'recommendation': 'r',
            'critical_finding': '1', 'critical_finding_text': 'Large pneumothorax'},
            follow_redirects=True)
        from app.models import CriticalFindingNotification, ClinicalAlert
        self.assertEqual(CriticalFindingNotification.query.count(), 1)
        self.assertTrue(ClinicalAlert.query.filter_by(alert_type='CRITICAL_RADIOLOGY').count() >= 1)
        self.assertTrue(Notification.query.filter_by(user_id=doc.id, notification_type='critical').count() >= 1)
        self.client.post(f'/radiology/orders/{o.id}/sign', follow_redirects=True)
        o = RadiologyOrder.query.get(o.id)
        self.assertEqual(o.status, 'Signed')
        self.assertEqual(o.report.status, 'Signed')
        self.assertEqual(Task.query.filter_by(related_resource_type='radiology_report_task').first().status, 'COMPLETED')
        # Amending the signed report keeps a version of the original text.
        self.client.post(f'/radiology/orders/{o.id}/report', data={
            'amend': '1', 'reason': 'typo', 'findings': 'f2', 'impression': 'i2', 'recommendation': 'r2'},
            follow_redirects=True)
        from app.models import RadiologyReportVersion
        v = RadiologyReportVersion.query.first()
        self.assertIsNotNone(v)
        self.assertEqual(v.impression, 'Large pneumothorax')
        self.assertEqual(RadiologyReport.query.first().status, 'Draft')


if __name__ == '__main__':
    unittest.main()
