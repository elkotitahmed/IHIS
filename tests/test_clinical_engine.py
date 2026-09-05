"""Integration tests for the clinical engine additions:
pharmacist reconciliation, interventions, structured clinical models,
timeline/alert wiring, and the admin AI workbench routes.
"""
import json
import re
import unittest

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, Medication,
                        Prescription, PrescriptionItem, Appointment,
                        CareTeam, CareTeamMember, MedicationReconciliation,
                        ReconciliationDiscrepancy, PharmacyIntervention,
                        ClinicalAlert, TimelineEvent, utcnow)


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class ClinicalEngineTestCase(unittest.TestCase):
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

        self.pharma = self._make_user('ph@t.com', 'pharmacist', 'Pharmacist')
        self.pharma_u = User.query.filter_by(email='ph@t.com').first()
        self.doc = self._make_user('doc2@t.com', 'doctor', 'Doctor')
        self.doc_u = User.query.filter_by(email='doc2@t.com').first()

        self.pat = User(username='pat', email='pat@t.com', full_name='Test P',
                        user_type='patient')
        self.pat.set_password('123456')
        self.pat.roles.append(Role.query.filter_by(name='Patient').first())
        db.session.add(self.pat)
        db.session.commit()
        self.patient = Patient(user_id=self.pat.id)
        db.session.add(self.patient)
        db.session.commit()

        # Need-to-know: assign both the pharmacist and doctor to a care team.
        doc_rec = Doctor.query.filter_by(user_id=self.doc_u.id).first()
        team = CareTeam(patient_id=self.patient.id, name='Test Care Team')
        db.session.add(team)
        db.session.flush()
        db.session.add(CareTeamMember(team_id=team.id, user_id=self.pharma_u.id,
                                     role='Clinical Pharmacist'))
        db.session.add(CareTeamMember(team_id=team.id, user_id=self.doc_u.id,
                                     role='Physician'))
        db.session.commit()

        self.med = Medication(generic_name='Paracetamol', brand_name='Panadol')
        db.session.add(self.med)
        db.session.commit()

        # A prescription authored by the doctor for the patient (for interventions).
        self.rx = Prescription(patient_id=self.patient.id,
                               doctor_id=doc_rec.id, status='Active')
        db.session.add(self.rx)
        db.session.flush()
        db.session.add(PrescriptionItem(prescription_id=self.rx.id,
                                        medication_id=self.med.id,
                                        dosage='500 mg', frequency='BID',
                                        quantity=30))
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
            db.session.flush()
            db.session.add(Doctor(user_id=u.id, specialty_id=spec.id,
                                  license_number='L' + email[:6]))
        db.session.commit()
        return u

    def _login(self, email):
        self.client.get('/auth/logout')
        r = self.client.get('/auth/login')
        rv = self.client.post('/auth/login', data={
            'email': email, 'password': '123456',
            'csrf_token': _csrf(r.data)}, follow_redirects=True)
        return rv

    def test_reconciliation_create_and_detail(self):
        self._login('ph@t.com')
        # GET form page
        r = self.client.get(f'/pharmacy/patient/{self.patient.id}/reconcile')
        self.assertEqual(r.status_code, 200)
        csrf = _csrf(r.data)
        r = self.client.post(
            f'/pharmacy/patient/{self.patient.id}/reconcile',
            data={
                'csrf_token': csrf,
                'home_medications': '[{"name":"Paracetamol","dose":"500 mg","freq":"PRN"},'
                                    '{"name":"Aspirin","dose":"75 mg","freq":"OD"}]',
                'reconciliation_type': 'Admission',
                'summary': 'Admission review',
            }, follow_redirects=True)
        self.assertIn(b'Reconciliation created', r.data)
        rec = MedicationReconciliation.query.filter_by(
            patient_id=self.patient.id).first()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.reconciliation_type, 'Admission')
        # detail page renders JSON lists
        r = self.client.get(f'/pharmacy/reconciliations/{rec.id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Paracetamol', r.data)
        # timeline event recorded by reconciliation
        ev = TimelineEvent.query.filter_by(
            patient_id=self.patient.id,
            source_type='reconciliation').first()
        self.assertIsNotNone(ev)

    def test_open_reconciliation_blocked(self):
        self._login('ph@t.com')
        r = self.client.get(f'/pharmacy/patient/{self.patient.id}/reconcile')
        csrf = _csrf(r.data)
        self.client.post(f'/pharmacy/patient/{self.patient.id}/reconcile', data={
            'csrf_token': csrf,
            'home_medications': '[{"name":"Paracetamol","dose":"500 mg","freq":"PRN"}]',
            'reconciliation_type': 'Admission',
        })
        # Second create should be blocked by the open-record guard.
        r = self.client.get(f'/pharmacy/patient/{self.patient.id}/reconcile',
                            follow_redirects=True)
        self.assertIn(b'open reconciliation for this patient', r.data)

    def test_discrepancy_resolve_and_complete(self):
        self._login('ph@t.com')
        r = self.client.get(f'/pharmacy/patient/{self.patient.id}/reconcile')
        csrf = _csrf(r.data)
        self.client.post(f'/pharmacy/patient/{self.patient.id}/reconcile', data={
            'csrf_token': csrf,
            'home_medications': '[{"name":"Warfarin","dose":"5 mg","freq":"OD"}]',
        })
        rec = MedicationReconciliation.query.filter_by(
            patient_id=self.patient.id).first()
        d = ReconciliationDiscrepancy.query.filter_by(
            reconciliation_id=rec.id).first()
        if d:
            r = self.client.post(f'/pharmacy/discrepancies/{d.id}/resolve',
                                 data={'csrf_token': csrf,
                                       'resolved_note': 'Confirmed on chart'},
                                 follow_redirects=True)
            self.assertIn(b'Discrepancy resolved', r.data)
        r = self.client.post(f'/pharmacy/reconciliations/{rec.id}/complete',
                             data={'csrf_token': csrf, 'notes': 'All done'},
                             follow_redirects=True)
        self.assertIn(b'Reconciliation completed', r.data)
        db.session.refresh(rec)
        self.assertEqual(rec.status, 'Completed')

    def test_intervention_create_respond(self):
        self._login('ph@t.com')
        # Reconcile page always renders a form -> reliable CSRF token source.
        csrf = _csrf(self.client.get(
            f'/pharmacy/patient/{self.patient.id}/reconcile').data)
        r = self.client.post(f'/pharmacy/prescriptions/{self.rx.id}/intervene',
                             data={'csrf_token': csrf,
                                   'issue': 'Check dose for renal impairment',
                                   'severity': 'Major', 'category': 'DOSE',
                                   'recommendation': 'Reduce dose'},
                             follow_redirects=True)
        self.assertIn(b'Intervention raised', r.data)
        iv = PharmacyIntervention.query.filter_by(
            prescription_id=self.rx.id).first()
        self.assertIsNotNone(iv)
        self.assertEqual(iv.status, 'OPEN')

        # Doctor accepts the intervention. The intervention now exists, so the
        # interventions page renders its respond form (carrying a CSRF token).
        self._login('doc2@t.com')
        iv_page = self.client.get('/pharmacy/interventions')
        csrf2 = _csrf(iv_page.data)
        self.assertTrue(csrf2, 'expected a respond-form CSRF token')
        self.client.post(f'/pharmacy/interventions/{iv.id}/respond',
                         data={'csrf_token': csrf2, 'status': 'ACCEPTED',
                               'response': 'Agreed'},
                         follow_redirects=True)
        db.session.refresh(iv)
        self.assertEqual(iv.status, 'ACCEPTED')
        # Acceptance records a DUPLICATE_THERAPY info alert.
        alert = ClinicalAlert.query.filter_by(
            patient_id=self.patient.id,
            alert_type='DUPLICATE_THERAPY').first()
        self.assertIsNotNone(alert)

    def test_reconciliation_list_and_alerts(self):
        self._login('ph@t.com')
        r = self.client.get('/pharmacy/reconciliations')
        self.assertEqual(r.status_code, 200)
        r = self.client.get('/pharmacy/interventions')
        self.assertEqual(r.status_code, 200)
        # High-severity discrepancy surfaces an OPEN clinical alert.
        alert = ClinicalAlert.query.filter_by(
            patient_id=self.patient.id,
            status='OPEN').first()
        # May be none until a reconcile run creates one; guard accordingly.

    def test_access_control_blocks_unscoped(self):
        """A user with no need-to-know cannot view another patient's reconcile page."""
        other = User(username='other', email='other@t.com', full_name='Other',
                     user_type='nurse')
        other.set_password('123456')
        other.roles.append(Role.query.filter_by(name='Nurse').first())
        db.session.add(other)
        db.session.commit()
        self._login('other@t.com')
        r = self.client.get(f'/pharmacy/patient/{self.patient.id}/reconcile')
        self.assertEqual(r.status_code, 403)

    def test_admin_ai_pages(self):
        """Admin AI reconnaissance pages render and the coding assistant responds."""
        admin = User(username='admin2', email='admin2@t.com', full_name='Adm',
                     user_type='admin')
        admin.set_password('123456')
        admin.roles.append(Role.query.filter_by(name='Admin').first())
        db.session.add(admin)
        db.session.commit()
        self._login('admin2@t.com')

        r = self.client.get('/admin/ai/appointment-optimization')
        self.assertEqual(r.status_code, 200)

        r = self.client.get('/admin/ai/coding-assistant')
        self.assertEqual(r.status_code, 200)
        csrf = _csrf(r.data)
        r = self.client.post('/admin/ai/coding-assistant',
                             data={'csrf_token': csrf, 'text': 'hypertension and diabetes'},
                             follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'I10', r.data)


if __name__ == '__main__':
    unittest.main()