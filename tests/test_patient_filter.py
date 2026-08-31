"""Verify the Doctor patient-list is filtered to need-to-know patients.

The `/doctor/patients` list must only show patients the logged-in Doctor has a
documented relationship with (so clicking Overview/Detail never 403s), while
Admin/SuperAdmin keep full supervisory search.
"""
import unittest
from datetime import datetime

from app import create_app, db
from app.models import Role, Specialty, Doctor, User, Patient, Appointment
from seed import ROLES


class DoctorPatientFilterTest(unittest.TestCase):
    def _login(self, client, email):
        resp = client.post('/auth/login', data={
            'email': email, 'password': '123456',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200, f'login failed for {email}')
        return client

    def test_doctor_sees_only_own_patients(self):
        app = create_app('testing')
        app.config['WTF_CSRF_ENABLED'] = False
        with app.app_context():
            db.create_all()
            for n in ROLES:
                db.session.add(Role(name=n))
            db.session.commit()

            def mk_user(uname, utype, roles_lst):
                u = User(username=uname, email=f'{uname}@test.com',
                         full_name=f'Test {uname}', user_type=utype)
                u.set_password('123456')
                for r in roles_lst:
                    u.roles.append(Role.query.filter_by(name=r).first())
                db.session.add(u)
                db.session.flush()
                return u

            u_doc = mk_user('doctor', 'doctor', ['Doctor'])
            spec = Specialty(name='General')
            db.session.add(spec)
            db.session.flush()
            doctor = Doctor(user_id=u_doc.id, specialty_id=spec.id)
            db.session.add(doctor)

            u_pat1 = mk_user('patient1', 'patient', ['Patient'])
            pat1 = Patient(user_id=u_pat1.id)
            db.session.add(pat1)
            u_pat2 = mk_user('patient2', 'patient', ['Patient'])
            pat2 = Patient(user_id=u_pat2.id)
            db.session.add(pat2)
            u_admin = mk_user('admin', 'admin', ['Admin'])
            db.session.commit()

            # Doctor related only to pat1, not pat2
            db.session.add(Appointment(patient_id=pat1.id, doctor_id=doctor.id,
                                       scheduled_at=datetime.now()))
            db.session.commit()

            client = app.test_client()

            self._login(client, 'doctor@test.com')
            html = client.get('/doctor/patients').get_data(as_text=True)
            self.assertIn(pat1.user.full_name, html)
            self.assertNotIn(pat2.user.full_name, html)
            # overview of related patient works, unrelated is hidden (403 direct)
            self.assertEqual(client.get(f'/doctor/patients/{pat1.id}/overview').status_code, 200)
            self.assertEqual(client.get(f'/doctor/patients/{pat2.id}/overview').status_code, 403)

            client.get('/auth/logout')
            self._login(client, 'admin@test.com')
            html = client.get('/doctor/patients').get_data(as_text=True)
            self.assertIn(pat1.user.full_name, html)
            self.assertIn(pat2.user.full_name, html)


if __name__ == '__main__':
    unittest.main()
