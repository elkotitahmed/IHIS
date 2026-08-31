"""iHIS advanced tests: care coordination, AI, reports, REST API, security, profile."""
import re
import unittest
from datetime import datetime

from app import create_app, db
from app.models import (
    User, Role, Patient, Doctor, Specialty, Medication, MedicalRecord,
    Prescription, PrescriptionItem, LabTestCatalog, LabOrder, LabResult, ImagingType,
    RadiologyOrder, RadiologyReport, Referral, CareTeamMember,
    MultidisciplinaryCase, LoginAttempt, Appointment, PatientDocument, VitalSign,
)

ROLES = ['SuperAdmin', 'Admin', 'Doctor', 'Nurse', 'Patient',
         'LabTechnician', 'Radiologist', 'Pharmacist', 'Receptionist',
         'Dentist', 'Physiotherapist']


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class AdvancedTestCase(unittest.TestCase):
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
        self.nurse = self._make_user('nurse@test.com', 'nurse', 'Nurse')
        self.pat = self._make_user('patient@test.com', 'patient', 'Patient')
        self.admin = self._make_user('admin@test.com', 'admin', 'Admin')
        self.superadmin = self._make_user('root@test.com', 'admin', 'SuperAdmin')
        self.pharma = self._make_user('pharma@test.com', 'pharmacist', 'Pharmacist')

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
        resp = self.client.post('/auth/login', data={
            'email': email, 'password': password, 'csrf_token': tok,
        }, follow_redirects=True)
        # Capture a session-stable CSRF token from a page common to all roles
        # (profile is reachable by every logged-in user), for API writes.
        self._csrf = _csrf(self.client.get('/auth/profile').data)
        return resp

    def _logout(self):
        self.client.get('/auth/logout')

    def _link_doc(self, patient_id):
        """Establish a documented doctor<->patient relationship (an appointment)
        so the doctor has need-to-know access to the patient's record."""
        appt = Appointment(patient_id=patient_id, doctor_id=self.doctor.id,
                           scheduled_at=datetime.now())
        db.session.add(appt)
        db.session.commit()

    def _link_nurse(self, patient_id):
        """Establish a documented nurse<->patient relationship (a vital-signs
        encounter) so the nurse has need-to-know access to the patient."""
        db.session.add(VitalSign(patient_id=patient_id, nurse_id=self.nurse.id))
        db.session.commit()

    # ---------------- Care coordination ----------------
    def test_care_referrals_list_and_create(self):
        self._link_doc(self.patient.id)
        self._login(self.doc.email)
        r = self.client.get('/care/referrals')
        self.assertEqual(r.status_code, 200)
        page = self.client.get('/care/referrals')
        tok = _csrf(page.data)
        r = self.client.post('/care/referrals/new', data={
            'csrf_token': tok, 'patient_id': self.patient.id,
            'to_specialty': 'Cardiology', 'reason': 'Evaluate chest pain',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Referral.query.count(), 1)

    def test_care_team_add_and_remove_member(self):
        self._link_nurse(self.patient.id)
        self._login(self.nurse.email)
        page = self.client.get(f'/care/teams/{self.patient.id}')
        self.assertEqual(page.status_code, 200)
        tok = _csrf(page.data)
        r = self.client.post(f'/care/teams/{self.patient.id}/add-member', data={
            'csrf_token': tok, 'user_id': self.doc.id, 'role': 'Primary Physician',
        })
        self.assertIn(r.status_code, (200, 302))
        self.assertEqual(CareTeamMember.query.count(), 1)

    def test_care_cases_create(self):
        self._link_doc(self.patient.id)
        self._login(self.doc.email)
        r = self.client.get('/care/cases')
        self.assertEqual(r.status_code, 200)
        page = self.client.get('/care/cases')
        tok = _csrf(page.data)
        self.client.post('/care/cases/new', data={
            'csrf_token': tok, 'patient_id': self.patient.id,
            'title': 'Complex case', 'description': 'Multi-specialty review',
        })
        self.assertEqual(MultidisciplinaryCase.query.count(), 1)

    def test_care_patient_denied(self):
        self._login(self.pat.email)
        r = self.client.get('/care/referrals')
        self.assertEqual(r.status_code, 403)

    # ---------------- AI ----------------
    def test_ai_doctor(self):
        self._link_doc(self.patient.id)
        self._login(self.doc.email)
        self.assertEqual(self.client.get(f'/ai/summary/{self.patient.id}').status_code, 200)
        self.assertEqual(self.client.get(f'/ai/diagnosis-support/{self.patient.id}').status_code, 200)
        # missing lab order -> graceful redirect back to lab orders (not a crash)
        r = self.client.get('/ai/lab/1')
        self.assertEqual(r.status_code, 302)

    def test_ai_patient_and_admin_rbac(self):
        self._login(self.pat.email)
        self.assertEqual(self.client.get('/ai/health-insights').status_code, 200)
        self.assertEqual(self.client.get('/ai/analytics').status_code, 403)
        self._logout()
        self._login(self.doc.email)
        # doctor is clinical: summary ok; patient-only insights blocked; analytics admin-only blocked
        self.assertEqual(self.client.get('/ai/health-insights').status_code, 403)
        self.assertEqual(self.client.get('/ai/analytics').status_code, 403)
        self._logout()
        self._login(self.admin.email)
        self.assertEqual(self.client.get('/ai/analytics').status_code, 200)

    # ---------------- Reports ----------------
    def _seed_clinical_reports(self):
        spec = Specialty(name='Cardiology')
        db.session.add(spec)
        db.session.commit()
        doc = Doctor.query.filter_by(user_id=self.doc.id).first()
        med = Medication(generic_name='Paracetamol', brand_name='Panadol', category='Analgesic')
        db.session.add(med)
        db.session.commit()
        record = MedicalRecord(patient_id=self.patient.id, doctor_id=doc.id,
                               diagnosis='Hypertension', treatment_plan='Medication')
        db.session.add(record)
        db.session.commit()
        rx = Prescription(patient_id=self.patient.id, doctor_id=doc.id, status='Active')
        db.session.add(rx)
        db.session.flush()
        db.session.add(PrescriptionItem(prescription_id=rx.id, medication_id=med.id,
                                        dosage='500mg', frequency='twice daily', status='Active'))
        db.session.commit()
        test = LabTestCatalog(test_name='CBC', normal_range='4-11', unit='x10^9/L')
        db.session.add(test)
        db.session.commit()
        lo = LabOrder(patient_id=self.patient.id, doctor_id=doc.id, test_id=test.id, status='Completed')
        db.session.add(lo)
        db.session.commit()
        db.session.add(LabResult(order_id=lo.id, result_value='8.2', is_abnormal=True))
        img = ImagingType(name='X-Ray')
        db.session.add(img)
        db.session.commit()
        ro = RadiologyOrder(patient_id=self.patient.id, doctor_id=doc.id,
                            imaging_type_id=img.id, status='Completed')
        db.session.add(ro)
        db.session.commit()
        db.session.add(RadiologyReport(order_id=ro.id, findings='Normal', impression='Clear',
                                       reported_by=self.doc.id))
        db.session.commit()
        self.lo, self.ro, self.rx = lo, ro, rx

    def test_reports_dashboard_and_pdf(self):
        self._seed_clinical_reports()
        self._login(self.doc.email)
        self.assertEqual(self.client.get('/reports/').status_code, 200)
        for path in [f'/reports/medical-record/{MedicalRecord.query.first().id}',
                     f'/reports/lab-result/{self.lo.id}',
                     f'/reports/radiology-report/{self.ro.id}',
                     f'/reports/prescription/{self.rx.id}']:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn('application/pdf', r.headers.get('Content-Type', ''))
        # patient can see dashboard but not admin statistics
        self._logout()
        self._login(self.pat.email)
        self.assertEqual(self.client.get('/reports/').status_code, 200)
        self.assertEqual(self.client.get('/reports/statistics').status_code, 403)
        # admin can see statistics
        self._logout()
        self._login(self.admin.email)
        self.assertEqual(self.client.get('/reports/statistics').status_code, 200)

    # ---------------- Security: lockout ----------------
    def test_account_lockout(self):
        for _ in range(5):
            self._login(self.nurse.email, password='wrongpass')
        u = User.query.filter_by(email=self.nurse.email).first()
        self.assertIsNotNone(u.locked_until)
        # correct password now rejected while locked
        r = self._login(self.nurse.email, password='123456')
        self.assertIn('locked', r.data.decode().lower())
        self.assertGreaterEqual(LoginAttempt.query.count(), 5)
        # unlock manually -> login works again
        u.locked_until = None
        u.failed_login_attempts = 0
        db.session.commit()
        r = self._login(self.nurse.email, password='123456')
        self.assertEqual(r.status_code, 200)

    # ---------------- Security: permissions_required ----------------
    def test_superadmin_permissions_gated(self):
        # SuperAdmin holds all seeded permissions (mirrors seed.py behaviour)
        from app.models import Permission
        role = Role.query.filter_by(name='SuperAdmin').first()
        perm = Permission(name='manage_system', resource='system', action='manage')
        db.session.add(perm)
        db.session.commit()
        Role.query.filter_by(name='SuperAdmin').first().permissions.append(perm)
        db.session.commit()
        self._login(self.superadmin.email)
        self.assertEqual(self.client.get('/super-admin/permissions').status_code, 200)
        # regular admin blocked from super-admin-only portal
        self._logout()
        self._login(self.admin.email)
        self.assertEqual(self.client.get('/super-admin/dashboard').status_code, 403)

    # ---------------- REST API ----------------
    def test_api_public_endpoints(self):
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.assertEqual(self.client.get('/api/doctors').status_code, 200)
        # /api/patients exposes PHI and must require authentication
        self.assertEqual(self.client.get('/api/patients').status_code, 302)
        self._login(self.doc.email)
        self.assertEqual(self.client.get('/api/patients').status_code, 200)
        self._logout()
        self.assertEqual(self.client.get('/api/medications').status_code, 200)
        self.assertEqual(self.client.get('/api/lab-tests').status_code, 200)

    def test_public_registration_is_patient_only(self):
        # Valid patient self-registration works and yields a Patient (never Admin).
        page = self.client.get('/auth/register')
        tok = _csrf(page.data)
        r = self.client.post('/auth/register', data={
            'csrf_token': tok,
            'full_name': 'New Patient',
            'username': 'newpatient',
            'email': 'new@test.com',
            'phone': '',
            'gender': '',
            'user_type': 'patient',
            'password': 'secret123',
            'confirm_password': 'secret123',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        u = User.query.filter_by(email='new@test.com').first()
        self.assertIsNotNone(u)
        self.assertEqual(u.user_type, 'patient')
        self.assertTrue(u.has_any_role('Patient'))
        self.assertFalse(u.has_any_role('Admin'))

        # A crafted staff-role POST must create NO staff account at all.
        before = User.query.count()
        page = self.client.get('/auth/register')
        tok = _csrf(page.data)
        self.client.post('/auth/register', data={
            'csrf_token': tok,
            'full_name': 'Evil Admin',
            'username': 'eviladmin',
            'email': 'evil@test.com',
            'phone': '',
            'gender': '',
            'user_type': 'admin',
            'password': 'secret123',
            'confirm_password': 'secret123',
        }, follow_redirects=True)
        self.assertIsNone(User.query.filter_by(email='evil@test.com').first())
        self.assertEqual(User.query.count(), before)

    def test_appointment_api_patient_cannot_impersonate(self):
        # A patient may only book for themselves; status is server-controlled.
        self._login(self.pat.email)
        other = self._make_user('otherp@test.com', 'patient', 'Patient')
        other_p = Patient.query.filter_by(user_id=other.id).first()
        r = self.client.post('/api/appointments',
                             headers={'X-CSRFToken': self._csrf},
                             json={
            'patient_id': other_p.id, 'doctor_id': self.doctor.id,
            'scheduled_at': '2099-01-01T09:00:00', 'status': 'Completed'})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Appointment.query.filter_by(patient_id=self.patient.id).count(), 1)
        self.assertEqual(Appointment.query.filter_by(patient_id=other_p.id).count(), 0)
        created = Appointment.query.filter_by(patient_id=self.patient.id).first()
        self.assertEqual(created.status, 'Scheduled')  # client status ignored

    def test_api_protected_requires_login(self):
        r = self.client.get('/api/appointments')
        self.assertEqual(r.status_code, 302)  # redirect to login
        self._login(self.pat.email)
        self.assertEqual(self.client.get('/api/appointments').status_code, 200)

    def test_api_patient_scoped_access(self):
        # patient can read own detail + records
        self._login(self.pat.email)
        self.assertEqual(self.client.get(f'/api/patients/{self.patient.id}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/patients/{self.patient.id}/records').status_code, 200)
        # patient cannot read another patient's data via API
        other = self._make_user('other@test.com', 'patient', 'Patient')
        other_patient = Patient.query.filter_by(user_id=other.id).first()
        self.assertEqual(self.client.get(f'/api/patients/{other_patient.id}').status_code, 403)

    def test_api_post_writes(self):
        self._link_doc(self.patient.id)
        med = Medication(generic_name='Ibuprofen', brand_name='Brufen', category='Analgesic')
        db.session.add(med)
        db.session.commit()
        # patient cannot create prescription
        self._login(self.pat.email)
        r = self.client.post('/api/prescriptions', headers={'X-CSRFToken': self._csrf}, json={
            'patient_id': self.patient.id, 'medication_id': med.id})
        self.assertEqual(r.status_code, 403)
        self._logout()
        # doctor can
        self._login(self.doc.email)
        r = self.client.post('/api/prescriptions', headers={'X-CSRFToken': self._csrf}, json={
            'patient_id': self.patient.id,
            'items': [{'medication_id': med.id, 'dosage': '400mg',
                       'frequency': 'once daily', 'duration': '5 days', 'quantity': 1}]})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Prescription.query.count(), 1)

    def test_api_inventory_role(self):
        self._login(self.pharma.email)
        self.assertEqual(self.client.get('/api/inventory').status_code, 200)
        self._logout()
        self._login(self.pat.email)
        self.assertEqual(self.client.get('/api/inventory').status_code, 403)

    # ---------------- Profile / password ----------------
    def test_profile_page_and_password_change(self):
        self._login(self.nurse.email)
        r = self.client.get('/auth/profile')
        self.assertEqual(r.status_code, 200)
        tok = _csrf(r.data)
        # wrong current password
        r = self.client.post('/auth/profile', data={
            'csrf_token': tok, 'current_password': 'nope', 'new_password': 'newpass123',
            'confirm_password': 'newpass123'}, follow_redirects=True)
        self.assertIn('incorrect', r.data.decode().lower())
        # success
        page = self.client.get('/auth/profile')
        tok = _csrf(page.data)
        self.client.post('/auth/profile', data={
            'csrf_token': tok, 'current_password': '123456', 'new_password': 'newpass123',
            'confirm_password': 'newpass123'}, follow_redirects=True)
        u = User.query.filter_by(email=self.nurse.email).first()
        self.assertTrue(u.check_password('newpass123'))

    # ---------------- Patient documents persistence ----------------
    def test_documents_persist_after_upload(self):
        from io import BytesIO
        from app.models import PatientDocument
        self._login(self.pat.email)
        r = self.client.get('/patient/documents')
        self.assertEqual(r.status_code, 200)
        tok = _csrf(r.data)
        r = self.client.post('/patient/documents', data={
            'csrf_token': tok,
            'title': 'Blood Test Report',
            'document_type': 'report',
            'document': (BytesIO(b'%PDF-1.4 fake'), 'blood.pdf'),
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        doc = PatientDocument.query.filter_by(patient_id=self.patient.id).first()
        self.assertIsNotNone(doc)
        self.assertEqual(doc.title, 'Blood Test Report')
        # Uploads live in the private uploads store (relative path), never public static.
        self.assertIn('medical_documents/', doc.file_url)
        self.assertNotIn('/static/', doc.file_url)
        # listed on a fresh GET
        self.assertIn(b'Blood Test Report', self.client.get('/patient/documents').data)
        # non-patient blocked
        self._logout()
        self._login(self.doc.email)
        self.assertEqual(self.client.get('/patient/documents').status_code, 403)

    # ---------------- Notifications ----------------
    def test_notifications_mark_all_read(self):
        from app.models import Notification
        for i in range(3):
            db.session.add(Notification(user_id=self.pat.id, title=f'N{i}',
                                        message='msg', notification_type='in-app',
                                        is_read=False))
        db.session.commit()
        self._login(self.pat.email)
        r = self.client.get('/notifications')
        self.assertEqual(r.status_code, 200)
        tok = _csrf(r.data)
        r = self.client.post('/notifications/mark-all-read', data={
            'csrf_token': tok}, follow_redirects=True)
        unread = Notification.query.filter_by(user_id=self.pat.id, is_read=False).count()
        self.assertEqual(unread, 0)
        self.assertIn(b'All notifications marked as read', r.data)

    # ---------------- Patient 360 & protected document download ----------------
    def test_patient_360_renders_for_doctor(self):
        self._link_doc(self.patient.id)
        self._login(self.doc.email)
        r = self.client.get(f'/doctor/patients/{self.patient.id}/360')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Patient 360', r.data)

    def test_document_download_requires_login_and_owner_only(self):
        doc = PatientDocument(patient_id=self.patient.id, title='X',
                              file_url='/static/uploads/medical_documents/x.txt')
        db.session.add(doc)
        db.session.commit()
        # unauthenticated -> redirect to login
        r = self.client.get(f'/patient/documents/{doc.id}/download')
        self.assertEqual(r.status_code, 302)
        # owner patient can access (file missing -> 404, but authorized)
        self._login(self.pat.email)
        r = self.client.get(f'/patient/documents/{doc.id}/download')
        self.assertEqual(r.status_code, 404)
        # a different patient is forbidden (403)
        other = self._make_user('p2@test.com', 'patient', 'Patient')
        self._logout()
        self._login(other.email)
        r = self.client.get(f'/patient/documents/{doc.id}/download')
        self.assertEqual(r.status_code, 403)

    # ---------------- Clinical record locking (RBAC + ownership) ----------------
    def test_lab_result_verify_locks_and_amend_requires_reason(self):
        from app.models import LabOrder, LabResult, LabTestCatalog, AuditLog
        cat = LabTestCatalog(test_name='CBC'); db.session.add(cat); db.session.commit()
        order = LabOrder(patient_id=self.patient.id, test_id=cat.id, status='Pending')
        db.session.add(order); db.session.commit()
        lab = self._make_user('lab@test.com', 'lab_technician', 'LabTechnician')
        self._login(lab.email)

        def post_result(extra=None):
            page = self.client.get(f'/lab/orders/{order.id}/result')
            data = {'csrf_token': _csrf(page.data), 'result_value': '12.4'}
            if extra:
                data.update(extra)
            return self.client.post(f'/lab/orders/{order.id}/result', data=data)

        post_result()
        # verify -> locked
        vpage = self.client.get(f'/lab/orders/{order.id}/result')
        self.client.post(f'/lab/orders/{order.id}/verify',
                         data={'csrf_token': _csrf(vpage.data)})
        res = LabResult.query.filter_by(order_id=order.id).first()
        self.assertEqual(res.status, 'Verified')
        # direct edit without amend is blocked (stays Verified)
        post_result({'result_value': '13.4'})
        self.assertEqual(LabResult.query.get(res.id).status, 'Verified')
        self.assertEqual(LabResult.query.get(res.id).result_value, '12.4')
        # amend with reason -> reopens as Draft and is audited
        post_result({'result_value': '13.4', 'amend': '1', 'reason': 'Recalibrated analyzer'})
        self.assertEqual(LabResult.query.get(res.id).status, 'Draft')
        self.assertEqual(LabResult.query.get(res.id).result_value, '13.4')
        self.assertTrue(AuditLog.query.filter_by(
            action='AMEND_LAB_RESULT', resource_id=res.id).count() >= 1)

    def test_radiology_report_sign_locks_and_amend_requires_reason(self):
        from app.models import RadiologyOrder, RadiologyReport, ImagingType, AuditLog
        img = ImagingType(name='X-Ray'); db.session.add(img); db.session.commit()
        order = RadiologyOrder(patient_id=self.patient.id, imaging_type_id=img.id,
                               status='Pending')
        db.session.add(order); db.session.commit()
        radio = self._make_user('radio@test.com', 'radiologist', 'Radiologist')
        self._login(radio.email)

        def post_report(extra=None):
            page = self.client.get(f'/radiology/orders/{order.id}/report')
            data = {'csrf_token': _csrf(page.data), 'findings': 'Clear',
                    'impression': 'Normal', 'recommendation': 'None'}
            if extra:
                data.update(extra)
            return self.client.post(f'/radiology/orders/{order.id}/report', data=data)

        post_report()
        spage = self.client.get(f'/radiology/orders/{order.id}/report')
        self.client.post(f'/radiology/orders/{order.id}/sign',
                         data={'csrf_token': _csrf(spage.data)})
        rep = RadiologyReport.query.filter_by(order_id=order.id).first()
        self.assertEqual(rep.status, 'Signed')
        # direct edit without amend is blocked (stays Signed)
        post_report({'impression': 'Changed'})
        self.assertEqual(RadiologyReport.query.get(rep.id).status, 'Signed')
        self.assertEqual(RadiologyReport.query.get(rep.id).impression, 'Normal')
        # amend with reason -> reopens as Draft and is audited
        post_report({'impression': 'Changed', 'amend': '1', 'reason': 'Prior study reviewed'})
        self.assertEqual(RadiologyReport.query.get(rep.id).status, 'Draft')
        self.assertEqual(RadiologyReport.query.get(rep.id).impression, 'Changed')
        self.assertTrue(AuditLog.query.filter_by(
            action='AMEND_RADIOLOGY_REPORT', resource_id=rep.id).count() >= 1)

    def test_medical_record_sign_locks_and_amend_requires_reason(self):
        from app.models import MedicalRecord, AuditLog
        self._link_doc(self.patient.id)
        self._login(self.doc.email)
        page = self.client.get(f'/doctor/patients/{self.patient.id}/emr/add')
        self.client.post(f'/doctor/patients/{self.patient.id}/emr/add', data={
            'csrf_token': _csrf(page.data),
            'diagnosis': 'Hypertension', 'treatment_plan': 'ACE inhibitor',
            'clinical_notes': 'BP controlled',
        }, follow_redirects=True)
        rec = MedicalRecord.query.filter_by(patient_id=self.patient.id).first()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, 'Draft')
        # doctor signs -> locked
        spage = self.client.get(f'/doctor/patients/{self.patient.id}')
        self.client.post(f'/doctor/records/{rec.id}/sign',
                         data={'csrf_token': _csrf(spage.data)}, follow_redirects=True)
        db.session.refresh(rec)
        self.assertEqual(rec.status, 'Signed')
        # edit signed record without reason -> blocked, stays Signed
        epage = self.client.get(f'/doctor/records/{rec.id}/edit')
        self.client.post(f'/doctor/records/{rec.id}/edit', data={
            'csrf_token': _csrf(epage.data),
            'diagnosis': 'Changed', 'treatment_plan': 'x', 'clinical_notes': 'y',
        }, follow_redirects=True)
        db.session.refresh(rec)
        self.assertEqual(rec.status, 'Signed')
        self.assertEqual(rec.diagnosis, 'Hypertension')  # unchanged
        # amend with reason -> reopens as Draft and is audited
        epage = self.client.get(f'/doctor/records/{rec.id}/edit')
        self.client.post(f'/doctor/records/{rec.id}/edit', data={
            'csrf_token': _csrf(epage.data),
            'diagnosis': 'Stage 2 HTN', 'treatment_plan': 'Updated',
            'clinical_notes': 'GHB added', 'reason': 'Patient response changed',
        }, follow_redirects=True)
        db.session.refresh(rec)
        self.assertEqual(rec.status, 'Draft')
        self.assertEqual(rec.diagnosis, 'Stage 2 HTN')
        self.assertTrue(AuditLog.query.filter_by(
            action='AMEND_MEDICAL_RECORD', resource_id=rec.id).count() >= 1)

    def test_prescription_cancel_and_dispense_lockdown(self):
        from app.models import Medication, Prescription, PrescriptionItem
        self._link_doc(self.patient.id)
        med = Medication(generic_name='Amoxicillin'); db.session.add(med); db.session.commit()
        rx = Prescription(patient_id=self.patient.id, status='Active')
        db.session.add(rx); db.session.commit()
        db.session.add(PrescriptionItem(prescription_id=rx.id, medication_id=med.id,
                                        quantity=1))
        db.session.commit()
        # doctor cancels -> prescription + items become Cancelled
        self._login(self.doc.email)
        page = self.client.get(f'/doctor/patients/{self.patient.id}/prescriptions')
        self.client.post(f'/doctor/prescriptions/{rx.id}/cancel',
                         data={'csrf_token': _csrf(page.data), 'reason': 'drug allergy'},
                         follow_redirects=True)
        self.assertEqual(Prescription.query.get(rx.id).status, 'Cancelled')
        self.assertEqual(PrescriptionItem.query.filter_by(prescription_id=rx.id).first().status,
                         'Cancelled')
        # pharmacy cannot dispense a cancelled prescription
        self._logout()
        self._login(self.pharma.email)
        p = self.client.get('/pharmacy/prescriptions')
        self.client.post(f'/pharmacy/prescriptions/{rx.id}/dispense',
                         data={'csrf_token': _csrf(p.data)}, follow_redirects=True)
        item = PrescriptionItem.query.filter_by(prescription_id=rx.id).first()
        self.assertEqual(item.status, 'Cancelled')
        self.assertEqual(Prescription.query.get(rx.id).status, 'Cancelled')

    def test_permission_gates_write_routes_beyond_role(self):
        # Admin passes the role gate (Admin is in roles_required) but lacks the
        # fine-grained permission, so the write routes are denied (403).
        self._login(self.admin.email)
        self.assertEqual(self.client.get(f'/lab/orders/1/result').status_code, 403)
        self.assertEqual(self.client.get(f'/nursing/patients/{self.patient.id}/vitals').status_code, 403)
        self.assertEqual(self.client.get(f'/nursing/patients/{self.patient.id}/notes').status_code, 403)
        self.assertEqual(self.client.get(f'/radiology/orders/1/report').status_code, 403)

    def test_admin_dashboard_renders_real_kpis(self):
        # Bind some real activity so KPI figures are meaningful.
        from app.models import Appointment, Payment, Bill, BillItem
        self._link_doc(self.patient.id)  # creates an appointment (need-to-know)
        appt = Appointment.query.filter_by(patient_id=self.patient.id).first()
        self.assertEqual(appt.status, 'Scheduled')
        bill = Bill(patient_id=self.patient.id, source_type='Manual',
                    notes='dashboard test')
        db.session.add(bill); db.session.flush()
        db.session.add(BillItem(bill_id=bill.id, description='Consult', quantity=1, unit_price=150))
        db.session.add(Payment(bill_id=bill.id, amount=100, method='Cash'))
        db.session.commit()
        self._login(self.admin.email)
        r = self.client.get('/admin/dashboard')
        self.assertEqual(r.status_code, 200)
        body = r.data.decode()
        # KPIs render (patients, doctors, appointments, revenue)
        self.assertIn('Patients', body)
        self.assertIn('Doctors', body)
        self.assertIn('Appointments Today', body)
        self.assertIn('Revenue Overview', body)
        self.assertIn('100.00', body)  # payment rendered as revenue
        # the appointments doughnut payload uses tojson for the status labels
        self.assertIn('statusChart', body)


if __name__ == '__main__':
    unittest.main()