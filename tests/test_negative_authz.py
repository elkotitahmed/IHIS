"""Ownership / negative authorization regression tests.

Verifies the need-to-know policy is enforced server-side for clinical
mutations: a Doctor who has NO documented relationship to a patient cannot
sign or amend that patient's medical record (403), while the owning Doctor
(authored a record) can sign their own record.
"""
import unittest
from datetime import datetime

from app import create_app, db
from app.models import Role, Specialty, Doctor, User, Patient, MedicalRecord
from app.permissions import MEDICAL_RECORD_SIGN, MEDICAL_RECORD_AMEND
from seed import ROLES


class NegativeAuthzTest(unittest.TestCase):
    def _login(self, client, email):
        resp = client.post('/auth/login', data={
            'email': email, 'password': '123456',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200, f'login failed for {email}')
        return client

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

    def _mk_doctor_user(self, uname):
        u = self._mk_user(uname, 'doctor', ['Doctor'])
        spec = Specialty.query.filter_by(name='General').first()
        d = Doctor(user_id=u.id, specialty_id=spec.id if spec else None)
        db.session.add(d)
        db.session.flush()
        return u, d

    def test_unrelated_doctor_cannot_sign_or_amend_record(self):
        app = create_app('testing')
        app.config['WTF_CSRF_ENABLED'] = False
        with app.app_context():
            db.create_all()
            for n in ROLES:
                db.session.add(Role(name=n))
            db.session.add(Specialty(name='General'))
            db.session.commit()
            self._ensure_perms('Doctor', [MEDICAL_RECORD_SIGN, MEDICAL_RECORD_AMEND])

            u_pat = self._mk_user('pat', 'patient', ['Patient'])
            pat = Patient(user_id=u_pat.id)
            db.session.add(pat)
            db.session.flush()

            # owning doctor authors the record (documented relationship)
            u_owner, d_owner = self._mk_doctor_user('ownerdoc')
            rec = MedicalRecord(patient_id=pat.id, doctor_id=d_owner.id,
                                visit_date=datetime.now().date(),
                                diagnosis='Flue', status='Draft')
            db.session.add(rec)
            db.session.commit()

            # unrelated doctor has NO relationship to this patient
            u_other, _ = self._mk_doctor_user('otherdoc')
            db.session.commit()

            client = app.test_client()

            # unrelated doctor: sign denied (403)
            self._login(client, 'otherdoc@test.com')
            self.assertEqual(
                client.post(f'/doctor/records/{rec.id}/sign').status_code, 403)
            # unrelated doctor: edit/amend denied (403)
            self.assertEqual(
                client.post(f'/doctor/records/{rec.id}/edit', data={
                    'diagnosis': 'X', 'treatment_plan': '', 'clinical_notes': '',
                }).status_code, 403)

            # owning doctor: can sign their own record
            client.get('/auth/logout')
            self._login(client, 'ownerdoc@test.com')
            self.assertEqual(
                client.post(f'/doctor/records/{rec.id}/sign', follow_redirects=True)
                .status_code, 200)
            # record is now signed/locked
            self.assertEqual(
                db.session.get(MedicalRecord, rec.id).status, 'Signed')


if __name__ == '__main__':
    unittest.main()