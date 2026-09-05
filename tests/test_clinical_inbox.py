"""Tests for the unified Clinical Inbox and result acknowledgement workflow."""
import re
import unittest
from datetime import datetime

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, CareTeam,
                        CareTeamMember, LabTestCatalog, LabOrder, LabResult,
                        ImagingType, RadiologyOrder, RadiologyReport,
                        ResultAcknowledgement, utcnow)


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class ClinicalInboxTestCase(unittest.TestCase):
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
        self.test = LabTestCatalog(test_name='CBC', is_active=True)
        self.img = ImagingType(name='Chest X-Ray')
        db.session.add_all([self.test, self.img])
        db.session.flush()

        self.doc = self._make_user('doc@t.com', 'doctor', 'Doctor')
        self.doc_profile = Doctor.query.filter_by(user_id=self.doc.id).first()
        self.pat = self._make_patient('pat@t.com')
        team = CareTeam(patient_id=self.pat.id, name='Team')
        db.session.add(team)
        db.session.flush()
        db.session.add(CareTeamMember(team_id=team.id, user_id=self.doc.id,
                                      role='Physician'))
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

    def _add_lab_result(self, critical=False, abnormal=False):
        order = LabOrder(patient_id=self.pat.id, doctor_id=self.doc_profile.id,
                         test_id=self.test.id, status='FINALIZED')
        db.session.add(order)
        db.session.flush()
        res = LabResult(order_id=order.id, result_value='12.5',
                        result_unit='x10^9/L', status='Verified',
                        is_critical=critical, is_abnormal=abnormal,
                        result_date=utcnow())
        db.session.add(res)
        db.session.commit()
        return order, res

    def _add_radiology_report(self):
        order = RadiologyOrder(patient_id=self.pat.id, doctor_id=self.doc_profile.id,
                               imaging_type_id=self.img.id, status='FINALIZED')
        db.session.add(order)
        db.session.flush()
        rep = RadiologyReport(order_id=order.id, findings='normal',
                              status='Signed', report_date=utcnow())
        db.session.add(rep)
        db.session.commit()
        return order, rep

    def _login(self, email):
        self.client.get('/auth/logout')
        r = self.client.get('/auth/login')
        return self.client.post('/auth/login', data={
            'email': email, 'password': '123456',
            'csrf_token': _csrf(r.data)}, follow_redirects=True)

    def test_inbox_lists_unreviewed_results(self):
        self._login('doc@t.com')
        self._add_lab_result(critical=True)
        self._add_radiology_report()
        r = self.client.get('/clinical/inbox')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'CBC', r.data)
        self.assertIn(b'Chest X-Ray', r.data)

    def test_lab_ack_recorded_and_critical_kind(self):
        self._login('doc@t.com')
        order, res = self._add_lab_result(critical=True)
        inbox = self.client.get('/clinical/inbox')
        ack = ResultAcknowledgement.query.filter_by(
            result_type='LAB', result_id=res.id).first()
        self.assertIsNone(ack)
        self.client.post('/clinical/inbox/ack', data={
            'csrf_token': _csrf(inbox.data),
            'result_type': 'LAB', 'result_id': str(res.id),
            'patient_id': str(self.pat.id), 'note': 'Reviewed',
        }, follow_redirects=True)
        ack = ResultAcknowledgement.query.filter_by(
            result_type='LAB', result_id=res.id).first()
        self.assertIsNotNone(ack)
        self.assertEqual(ack.kind, 'CRITICAL')
        self.assertEqual(ack.note, 'Reviewed')

    def test_radiology_ack_recorded(self):
        self._login('doc@t.com')
        order, rep = self._add_radiology_report()
        inbox = self.client.get('/clinical/inbox')
        self.client.post('/clinical/inbox/ack', data={
            'csrf_token': _csrf(inbox.data),
            'result_type': 'RADIOLOGY', 'result_id': str(rep.id),
            'patient_id': str(self.pat.id),
        }, follow_redirects=True)
        ack = ResultAcknowledgement.query.filter_by(
            result_type='RADIOLOGY', result_id=rep.id).first()
        self.assertIsNotNone(ack)
        self.assertEqual(ack.kind, 'REVIEW')

    def test_ack_idempotent(self):
        self._login('doc@t.com')
        order, res = self._add_lab_result()
        inbox = self.client.get('/clinical/inbox')
        csrf = _csrf(inbox.data)
        data = {'csrf_token': csrf, 'result_type': 'LAB',
                'result_id': str(res.id), 'patient_id': str(self.pat.id)}
        self.client.post('/clinical/inbox/ack', data=data, follow_redirects=True)
        self.client.post('/clinical/inbox/ack', data=data, follow_redirects=True)
        count = ResultAcknowledgement.query.filter_by(
            result_type='LAB', result_id=res.id).count()
        self.assertEqual(count, 1)

    def test_ack_requires_patient_access(self):
        """Acknowledging an out-of-scope patient's result is blocked (403 via
        require_patient_access in the ack handler)."""
        self._login('doc@t.com')
        outsider = self._make_patient('outside@t.com')
        # Create a lab result for the outsider with a different doctor ordering.
        other_doc = self._make_user('odoc@t.com', 'doctor', 'Doctor')
        od = Doctor.query.filter_by(user_id=other_doc.id).first()
        o = LabOrder(patient_id=outsider.id, doctor_id=od.id,
                     test_id=self.test.id, status='FINALIZED')
        db.session.add(o)
        db.session.flush()
        res = LabResult(order_id=o.id, result_value='9.0', status='Verified',
                        result_date=utcnow())
        db.session.add(res)
        # Give the doctor their own in-scope result too, so the inbox renders an
        # ack form (and thus a valid CSRF token) — the outsider's result is not
        # listed because the doctor has no need-to-know for that patient.
        self._add_lab_result()
        db.session.commit()
        inbox = self.client.get('/clinical/inbox')
        r = self.client.post('/clinical/inbox/ack', data={
            'csrf_token': _csrf(inbox.data), 'result_type': 'LAB',
            'result_id': str(res.id), 'patient_id': str(outsider.id),
        })
        self.assertEqual(r.status_code, 403)


if __name__ == '__main__':
    unittest.main()
