"""Radiology safety module tests: CT/MRI safety evaluation, dose recording,
patient portal integration, and critical findings workflow."""
import re
import unittest
from datetime import datetime, date

from app import create_app, db
from app.models import (
    User, Role, Patient, Specialty, Doctor, ImagingType, RadiologyOrder,
    PatientImagingSafetyProfile, MRIImplantRegistry, ImagingDoseRecord,
    ContrastAdministration, ImagingSafetyScreening, ImagingReferenceLevel,
    CriticalFindingNotification, RadiologyReport,
)


class RadiologySafetyTestCase(unittest.TestCase):
    ROLES = ['SuperAdmin', 'Admin', 'Doctor', 'Nurse', 'Patient',
             'LabTechnician', 'Radiologist', 'Pharmacist', 'Receptionist',
             'Dentist', 'Physiotherapist', 'Cashier']

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
        self.admin_user = self._make_user('admin1@t.com', 'admin', 'Admin')
        self.doc_user = self._make_user('doc1@t.com', 'doctor', 'Doctor')
        self.rad_user = self._make_user('rad1@t.com', 'rad', 'Radiologist')
        self.patient_user = self._make_user('pat1@t.com', 'patient', 'Patient')
        self.patient = Patient(user_id=self.patient_user.id, mrn='SAF001')
        db.session.add(self.patient)
        db.session.commit()
        self.patient_id = self.patient.id
        self.img_ct = ImagingType(name='CT Head', price=250.0)
        self.img_mri = ImagingType(name='MRI Brain', price=500.0)
        self.img_xray = ImagingType(name='Chest X-Ray', price=80.0)
        db.session.add_all([self.img_ct, self.img_mri, self.img_xray])
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
        tok = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', page.data)
        tok = tok.group(1).decode() if tok else ''
        self.client.post('/auth/login', data={
            'email': email, 'password': '123456', 'csrf_token': tok,
        }, follow_redirects=True)

    def _csrf(self, html):
        m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
        return m.group(1).decode() if m else ''

    # --- Safety Profile Tests ---
    def test_safety_profile_created_on_first_eval(self):
        """Safety profile should be auto-created when first evaluated."""
        from app.services.radiology.safety_service import RadiologySafetyService
        svc = RadiologySafetyService()
        profile = svc.get_or_create_safety_profile(self.patient_id)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.patient_id, self.patient_id)
        self.assertEqual(profile.mri_screening_status, 'Not Screened')

    def test_ct_safety_no_warnings_when_clear(self):
        """CT evaluation should have no critical warnings for a clean profile."""
        from app.services.radiology.safety_service import RadiologySafetyService
        svc = RadiologySafetyService()
        result = svc.evaluate_ct_safety(self.patient_id)
        self.assertIn('warnings', result)
        self.assertIn('safety_status', result)
        critical = [w for w in result['warnings'] if w['severity'] == 'Critical']
        self.assertEqual(len(critical), 0)

    def test_ct_safety_renal_warning(self):
        """Low eGFR should trigger a renal impairment warning."""
        from app.services.radiology.safety_service import RadiologySafetyService
        svc = RadiologySafetyService()
        profile = svc.get_or_create_safety_profile(self.patient_id)
        profile.last_egfr = 25.0
        profile.renal_function_date = date.today()
        db.session.commit()
        result = svc.evaluate_ct_safety(self.patient_id)
        renal_warnings = [w for w in result['warnings'] if 'renal' in w.get('category', '')]
        self.assertTrue(len(renal_warnings) > 0)
        self.assertEqual(renal_warnings[0]['severity'], 'Critical')

    def test_ct_safety_contrast_reaction_warning(self):
        """Previous contrast reaction should generate a warning."""
        from app.services.radiology.safety_service import RadiologySafetyService
        svc = RadiologySafetyService()
        profile = svc.get_or_create_safety_profile(self.patient_id)
        profile.previous_contrast_reaction = True
        profile.contrast_reaction_details = 'Urticaria during CT abdomen'
        db.session.commit()
        result = svc.evaluate_ct_safety(self.patient_id)
        contrast_warnings = [w for w in result['warnings']
                             if w.get('category') == 'contrast']
        self.assertTrue(len(contrast_warnings) > 0)

    def test_mri_safety_unscreened_is_critical(self):
        """Unscreened MRI status should be Critical."""
        from app.services.radiology.safety_service import RadiologySafetyService
        svc = RadiologySafetyService()
        result = svc.evaluate_mri_safety(self.patient_id)
        screening_warnings = [w for w in result['warnings']
                              if w.get('category') == 'screening']
        self.assertTrue(len(screening_warnings) > 0)
        self.assertEqual(screening_warnings[0]['severity'], 'Critical')

    def test_mri_safety_mr_unsafe_implant(self):
        """MR Unsafe implant should generate a critical warning."""
        from app.services.radiology.safety_service import RadiologySafetyService
        svc = RadiologySafetyService()
        profile = svc.get_or_create_safety_profile(self.patient_id)
        profile.mri_screening_status = 'Safety Concern'
        db.session.commit()
        db.session.add(MRIImplantRegistry(
            patient_id=self.patient_id,
            device_name='Pacemaker Model X',
            mr_safety_class='MR Unsafe',
            verification_status='Pending',
        ))
        db.session.commit()
        result = svc.evaluate_mri_safety(self.patient_id)
        implant_warnings = [w for w in result['warnings']
                            if 'implant' in w.get('category', '').lower()
                            or 'device' in w.get('category', '').lower()]
        self.assertTrue(len(implant_warnings) > 0)
        self.assertEqual(implant_warnings[0]['severity'], 'Critical')

    def test_mri_safety_cleared_when_clean(self):
        """Cleared profile with no implants should show no critical MRI warnings."""
        from app.services.radiology.safety_service import RadiologySafetyService
        svc = RadiologySafetyService()
        profile = svc.get_or_create_safety_profile(self.patient_id)
        profile.mri_screening_status = 'Cleared'
        db.session.commit()
        result = svc.evaluate_mri_safety(self.patient_id)
        critical = [w for w in result['warnings'] if w['severity'] == 'Critical']
        self.assertEqual(len(critical), 0)

    # --- Dose Service Tests ---
    def test_dose_annual_summary_empty(self):
        """Annual summary should return zero studies for a new patient."""
        from app.services.radiology.dose_service import RadiationDoseService
        svc = RadiationDoseService()
        summary = svc.get_patient_annual_summary(self.patient_id)
        self.assertIsNotNone(summary)
        self.assertEqual(summary['total_studies'], 0)

    def test_dose_record_and_summary(self):
        """Recording a dose record should appear in annual summary."""
        from app.services.radiology.dose_service import RadiationDoseService
        svc = RadiationDoseService()
        # Create an order first
        order = RadiologyOrder(
            patient_id=self.patient_id,
            imaging_type_id=self.img_ct.id,
            doctor_id=Doctor.query.filter_by(user_id=self.doc_user.id).first().id,
            status='Performed',
            performed_at=datetime(2026, 1, 15, 10, 0),
        )
        db.session.add(order)
        db.session.commit()
        record = svc.record_dose(
            patient_id=self.patient_id,
            order_id=order.id,
            study_date=datetime(2026, 1, 15),
            modality='CT',
            body_region='Head',
            study_description='CT Head without contrast',
            ctdi_vol=45.2,
            dlp=850.0,
            effective_dose_est=1.8,
            is_estimated=True,
            ordering_physician_id=order.doctor_id,
        )
        db.session.commit()
        self.assertIsNotNone(record)
        self.assertEqual(record.dlp, 850.0)
        summary = svc.get_patient_annual_summary(self.patient_id)
        self.assertEqual(summary['total_studies'], 1)
        self.assertEqual(summary['radiation_studies'], 1)

    def test_dose_alerts_generated(self):
        """Dose alerts should be generated for high exposure."""
        from app.services.radiology.dose_service import RadiationDoseService
        svc = RadiationDoseService()
        # Create multiple high-dose records
        for i in range(3):
            order = RadiologyOrder(
                patient_id=self.patient_id,
                imaging_type_id=self.img_ct.id,
                doctor_id=Doctor.query.filter_by(user_id=self.doc_user.id).first().id,
                status='Performed',
                performed_at=datetime(2026, 1, 10 + i, 10, 0),
            )
            db.session.add(order)
            db.session.commit()
            svc.record_dose(
                patient_id=self.patient_id,
                order_id=order.id,
                study_date=datetime(2026, 1, 10 + i),
                modality='CT',
                body_region='Abdomen',
                ctdi_vol=25.0,
                dlp=900.0,
                effective_dose_est=4.5,
                is_estimated=True,
                ordering_physician_id=order.doctor_id,
            )
        db.session.commit()
        alerts = svc.generate_dose_alerts(self.patient_id)
        self.assertIsInstance(alerts, list)

    # --- Route Integration Tests ---
    def test_dose_dashboard_requires_login(self):
        resp = self.client.get('/radiology/dose-dashboard')
        self.assertIn(resp.status_code, (302, 401))

    def test_dose_dashboard_accessible_to_admin(self):
        self._login(self.admin_user.email)
        resp = self.client.get('/radiology/dose-dashboard')
        self.assertEqual(resp.status_code, 200)

    def test_safety_profile_route(self):
        self._login(self.admin_user.email)
        resp = self.client.get(f'/radiology/safety-profile/{self.patient_id}')
        self.assertEqual(resp.status_code, 200)

    def test_edit_safety_profile_get(self):
        self._login(self.admin_user.email)
        resp = self.client.get(f'/radiology/safety-profile/{self.patient_id}/edit')
        self.assertEqual(resp.status_code, 200)

    def test_critical_findings_route(self):
        self._login(self.admin_user.email)
        resp = self.client.get('/radiology/critical-findings')
        self.assertEqual(resp.status_code, 200)

    def test_protocols_route(self):
        self._login(self.admin_user.email)
        resp = self.client.get('/radiology/protocols')
        self.assertEqual(resp.status_code, 200)

    def test_reference_levels_route_admin(self):
        self._login(self.admin_user.email)
        resp = self.client.get('/radiology/reference-levels')
        self.assertEqual(resp.status_code, 200)

    def test_patient_my_radiology_route(self):
        self._login(self.patient_user.email)
        resp = self.client.get('/patient/my-radiology')
        self.assertEqual(resp.status_code, 200)

    def test_safety_profile_edit_post(self):
        self._login(self.admin_user.email)
        page = self.client.get(f'/radiology/safety-profile/{self.patient_id}/edit')
        tok = self._csrf(page.data)
        resp = self.client.post(
            f'/radiology/safety-profile/{self.patient_id}/edit',
            data={
                'csrf_token': tok,
                'pregnancy_status': 'Not Pregnant',
                'last_egfr': 72.5,
                'renal_function_date': '2026-01-01',
                'previous_contrast_reaction': 'on',
                'contrast_reaction_details': 'Mild rash',
                'previous_contrast_type': 'Iodinated',
                'mri_screening_status': 'Cleared',
                'ct_contraindications': '',
                'ct_precautions': '',
                'special_preparation_notes': '',
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        profile = PatientImagingSafetyProfile.query.filter_by(
            patient_id=self.patient_id).first()
        self.assertTrue(profile.previous_contrast_reaction)
        self.assertEqual(profile.last_egfr, 72.5)
        self.assertEqual(profile.mri_screening_status, 'Cleared')

    def test_reference_level_post(self):
        self._login(self.admin_user.email)
        page = self.client.get('/radiology/reference-levels')
        tok = self._csrf(page.data)
        resp = self.client.post('/radiology/reference-levels', data={
            'csrf_token': tok,
            'name': 'CT Head Adult',
            'modality': 'CT',
            'effective_dose_threshold': 2.0,
            'dlp_threshold': 1000,
            'age_group': 'Adult',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        level = ImagingReferenceLevel.query.filter_by(name='CT Head Adult').first()
        self.assertIsNotNone(level)
        self.assertEqual(level.modality, 'CT')

    def test_mri_implant_add_route(self):
        self._login(self.admin_user.email)
        page = self.client.get(f'/radiology/safety-profile/{self.patient_id}')
        tok = self._csrf(page.data)
        resp = self.client.post(
            f'/radiology/safety-profile/{self.patient_id}/implant/add',
            data={
                'csrf_token': tok,
                'device_name': 'Spinal Cord Stimulator',
                'mr_safety_class': 'MR Unsafe',
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        imp = MRIImplantRegistry.query.filter_by(
            patient_id=self.patient_id,
            device_name='Spinal Cord Stimulator').first()
        self.assertIsNotNone(imp)
        self.assertEqual(imp.mr_safety_class, 'MR Unsafe')

    # --- Non-Radiation Distinction Tests ---
    def test_mri_no_radiation_in_summary(self):
        """MRI studies should not contribute to radiation dose totals."""
        from app.services.radiology.dose_service import RadiationDoseService
        svc = RadiationDoseService()
        order = RadiologyOrder(
            patient_id=self.patient_id,
            imaging_type_id=self.img_mri.id,
            doctor_id=Doctor.query.filter_by(user_id=self.doc_user.id).first().id,
            status='Performed',
            performed_at=datetime(2026, 3, 1, 10, 0),
        )
        db.session.add(order)
        db.session.commit()
        svc.record_dose(
            patient_id=self.patient_id,
            order_id=order.id,
            study_date=datetime(2026, 3, 1),
            modality='MRI',
            body_region='Brain',
            study_description='MRI Brain without contrast',
            is_estimated=True,
            effective_dose_est=0.0,
            dose_value=0.0,
            dose_unit='mSv',
            ordering_physician_id=order.doctor_id,
        )
        db.session.commit()
        summary = svc.get_patient_annual_summary(self.patient_id)
        self.assertEqual(summary['total_studies'], 1)
        self.assertEqual(summary['radiation_studies'], 0)
        self.assertEqual(summary['non_radiation_studies'], 1)

    def test_xray_included_in_radiation(self):
        """X-ray studies should count as radiation studies."""
        from app.services.radiology.dose_service import RadiationDoseService
        svc = RadiationDoseService()
        order = RadiologyOrder(
            patient_id=self.patient_id,
            imaging_type_id=self.img_xray.id,
            doctor_id=Doctor.query.filter_by(user_id=self.doc_user.id).first().id,
            status='Performed',
            performed_at=datetime(2026, 2, 15, 14, 0),
        )
        db.session.add(order)
        db.session.commit()
        svc.record_dose(
            patient_id=self.patient_id,
            order_id=order.id,
            study_date=datetime(2026, 2, 15),
            modality='X-ray',
            body_region='Chest',
            dose_value=0.02,
            dose_unit='mSv',
            effective_dose_est=0.02,
            is_estimated=True,
            ordering_physician_id=order.doctor_id,
        )
        db.session.commit()
        summary = svc.get_patient_annual_summary(self.patient_id)
        self.assertEqual(summary['radiation_studies'], 1)

    # --- Critical Finding Notification Tests ---
    def test_critical_finding_acknowledgement(self):
        """Acknowledging a critical finding should update status."""
        order = RadiologyOrder(
            patient_id=self.patient_id,
            imaging_type_id=self.img_ct.id,
            doctor_id=Doctor.query.filter_by(user_id=self.doc_user.id).first().id,
            status='Reported',
        )
        db.session.add(order)
        db.session.commit()
        finding = CriticalFindingNotification(
            order_id=order.id,
            finding='Large right-sided pneumothorax',
            severity='Critical',
            identified_by=self.rad_user.id,
        )
        db.session.add(finding)
        db.session.commit()
        self.assertFalse(finding.acknowledged)
        self._login(self.admin_user.email)
        page = self.client.get('/radiology/critical-findings')
        tok = self._csrf(page.data)
        resp = self.client.post(
            f'/radiology/critical-findings/{finding.id}/acknowledge',
            data={'csrf_token': tok},
            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        db.session.refresh(finding)
        self.assertTrue(finding.acknowledged)
        self.assertEqual(finding.acknowledged_by, self.admin_user.id)


if __name__ == '__main__':
    unittest.main()
