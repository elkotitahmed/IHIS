"""Regression tests for production-readiness security fixes.

Covers:
1. Login ``?next=`` open-redirect protection (protocol-relative URLs rejected).
2. API-created prescriptions always start as ``Active`` (server-controlled
   status, not client-injected).
3. API-created referrals always start as ``Pending`` (server-controlled).
"""
import unittest

from app import create_app, db
from app.models import (
    Role, Specialty, Doctor, User, Patient, Referral, Medication,
    Prescription, Appointment,
)
from app.utils import utcnow


class SecurityFixTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self._seed_roles()
        self.client = self.app.test_client()
        self.csrf_token = None
        self.patient = self._mk_patient('pat', 'Patient', ['Patient'])
        self.doctor_user, self.doctor = self._mk_doctor('doc')

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _seed_roles(self):
        from seed import ROLES
        for name in ROLES:
            if not Role.query.filter_by(name=name).first():
                db.session.add(Role(name=name))
        db.session.commit()
        self._ensure_perms('Doctor', ['PRESCRIPTION_CREATE'])

    def _ensure_perms(self, role_name, perm_names):
        from app.models import Permission
        role = Role.query.filter_by(name=role_name).first()
        for pn in perm_names:
            p = Permission.query.filter_by(name=pn).first()
            if p is None:
                p = Permission(name=pn)
                db.session.add(p)
                db.session.flush()
            if p not in role.permissions:
                role.permissions.append(p)

    def _mk_user(self, uname, utype, roles_lst):
        u = User(username=uname, email=f'{uname}@test.com',
                 full_name=f'Test {uname}', user_type=utype)
        u.set_password('123456')
        for r in roles_lst:
            role = Role.query.filter_by(name=r).first()
            u.roles.append(role)
        db.session.add(u)
        db.session.flush()
        return u

    def _mk_patient(self, uname, utype, roles_lst):
        u = self._mk_user(uname, utype, roles_lst)
        p = Patient(user_id=u.id)
        db.session.add(p)
        db.session.flush()
        return p

    def _mk_doctor(self, uname):
        u = self._mk_user(uname, 'doctor', ['Doctor'])
        spec = Specialty.query.filter_by(name='General').first()
        d = Doctor(user_id=u.id, specialty_id=spec.id if spec else None)
        db.session.add(d)
        db.session.flush()
        return u, d

    def _grant_doctor_access(self):
        """Give the doctor a documented need-to-know relationship to the
        patient via an appointment, so API mutations pass authz."""
        db.session.add(Appointment(
            patient_id=self.patient.id, doctor_id=self.doctor.id,
            scheduled_at=utcnow(), status='Scheduled',
        ))

    def _mk_medication(self, name):
        m = Medication(generic_name=name)
        db.session.add(m)
        db.session.flush()
        return m

    def _login(self, email, password='123456'):
        page = self.client.get('/auth/login')
        import re
        m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', page.data)
        self.csrf_token = m.group(1).decode() if m else ''
        return self.client.post('/auth/login', data={
            'email': email, 'password': password, 'csrf_token': self.csrf_token,
        }, follow_redirects=True)

    # --- open redirect (RED) ---
    def _get_csrf(self):
        page = self.client.get('/auth/login')
        import re
        m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', page.data)
        return m.group(1).decode() if m else ''

    def test_login_redirect_rejects_protocol_relative_url(self):
        token = self._get_csrf()
        resp = self.client.post('/auth/login?next=//evil.example/phish', data={
            'email': self.patient.user.email, 'password': '123456',
            'csrf_token': token,
        })
        loc = resp.headers.get('Location', '')
        self.assertFalse('evil.example' in loc, f'open redirect to {loc!r}')

    def test_login_redirect_rejects_absolute_url(self):
        token = self._get_csrf()
        resp = self.client.post('/auth/login?next=https://evil.example/steal', data={
            'email': self.patient.user.email, 'password': '123456',
            'csrf_token': token,
        })
        loc = resp.headers.get('Location', '')
        self.assertFalse('evil.example' in loc, f'open redirect to {loc!r}')

    def test_login_redirect_allows_local_path(self):
        token = self._get_csrf()
        resp = self.client.post('/auth/login?next=/patient/dashboard', data={
            'email': self.patient.user.email, 'password': '123456',
            'csrf_token': token,
        })
        self.assertTrue(resp.location == '/patient/dashboard'
                        or '/patient/dashboard' in resp.headers.get('Location', ''),
                        f'unexpected redirect {resp.status_code}')

    # --- prescription status is server-controlled (YELLOW) ---
    def test_api_create_prescription_status_is_server_controlled(self):
        self._grant_doctor_access()
        med = self._mk_medication('Metformin')
        db.session.commit()
        self._login(self.doctor_user.email)
        resp = self.client.post(
            '/api/prescriptions',
            headers={'X-CSRFToken': self.csrf_token},
            json={
                'patient_id': self.patient.id,
                'refills': 0,
                'items': [{'medication_id': med.id, 'dosage': '500mg',
                           'frequency': 'Twice daily', 'duration': '7 days',
                           'quantity': 1}],
                'status': 'Cancelled',
            })
        # The attempted client status 'Cancelled' must be ignored; created as Active.
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
        rx = Prescription.query.filter_by(patient_id=self.patient.id).first()
        self.assertIsNotNone(rx)
        self.assertEqual(rx.status, 'Active',
                         f'client-injected status was accepted: {rx.status!r}')

    # --- referral status is server-controlled (YELLOW) ---
    def test_api_create_referral_status_is_server_controlled(self):
        self._grant_doctor_access()
        other_user, other_doc = self._mk_doctor('doc2')
        db.session.commit()
        self._login(self.doctor_user.email)
        resp = self.client.post(
            '/api/referrals',
            headers={'X-CSRFToken': self.csrf_token},
            json={
                'patient_id': self.patient.id,
                'to_specialty': 'Cardiology',
                'to_doctor_id': other_doc.id,
                'reason': 'review',
                'status': 'COMPLETED',
            })
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
        r = Referral.query.filter_by(patient_id=self.patient.id).first()
        self.assertIsNotNone(r)
        self.assertEqual(r.status, 'Pending',
                         f'client-injected referral status accepted: {r.status!r}')


if __name__ == '__main__':
    unittest.main()