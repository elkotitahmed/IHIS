"""Verify report download respects the need-to-know policy.

A staff member without a documented relationship to a patient must NOT be able
to download that patient's medical-record/report PDF; only the patient, an
Admin/SuperAdmin, or a clinician with a documented need-to-know may.
"""
import unittest
from datetime import datetime

from app import create_app, db
from app.models import Role, Specialty, Doctor, User, Patient, Appointment, MedicalRecord
from seed import ROLES


class ReportAccessTest(unittest.TestCase):
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
            u.roles.append(Role.query.filter_by(name=r).first())
        db.session.add(u)
        db.session.flush()
        return u

    def test_unrelated_staff_cannot_download_report(self):
        app = create_app('testing')
        app.config['WTF_CSRF_ENABLED'] = False
        with app.app_context():
            db.create_all()
            for n in ROLES:
                db.session.add(Role(name=n))
            db.session.commit()

            spec = Specialty(name='General')
            db.session.add(spec)
            db.session.flush()

            u_pat = self._mk_user('pat', 'patient', ['Patient'])
            pat = Patient(user_id=u_pat.id)
            db.session.add(pat)
            db.session.flush()

            # a receptionist with NO relationship to the patient
            u_rec = self._mk_user('reception', 'staff', ['Receptionist'])

            # a doctor with a documented relationship (medical record)
            u_doc = self._mk_user('doctor', 'doctor', ['Doctor'])
            spec = Specialty.query.filter_by(name='General').first()
            doctor = Doctor(user_id=u_doc.id,
                            specialty_id=spec.id if spec else None)
            db.session.add(doctor)
            db.session.flush()
            rec = MedicalRecord(patient_id=pat.id, doctor_id=doctor.id,
                                visit_date=datetime.now().date())
            db.session.add(rec)
            db.session.commit()

            client = app.test_client()

            # receptionist: report is off-limits
            self._login(client, 'reception@test.com')
            self.assertEqual(
                client.get(f'/reports/medical-record/{rec.id}').status_code, 403)

            # patient can fetch their own report
            client.get('/auth/logout')
            self._login(client, 'pat@test.com')
            self.assertEqual(
                client.get(f'/reports/medical-record/{rec.id}').status_code, 200)

            # related doctor can fetch it
            client.get('/auth/logout')
            self._login(client, 'doctor@test.com')
            self.assertEqual(
                client.get(f'/reports/medical-record/{rec.id}').status_code, 200)


if __name__ == '__main__':
    unittest.main()