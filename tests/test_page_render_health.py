"""Rendered-page regression checks for the role landing pages and base layout."""
import unittest

from app import create_app, db
from app.models import Doctor, Patient, Role, Specialty, User
from app.permissions import seed_permissions
from seed import ROLES


ACCOUNTS = (
    ('admin', 'admin', 'Admin', '/admin/dashboard', 'Admin Dashboard'),
    ('doctor', 'doctor', 'Doctor', '/doctor/dashboard', 'Physician Dashboard'),
    ('nurse', 'nurse', 'Nurse', '/nursing/dashboard', 'Nursing Dashboard'),
    ('lab', 'lab_technician', 'LabTechnician', '/lab/dashboard', 'Laboratory'),
    ('radio', 'radiologist', 'Radiologist', '/radiology/dashboard', 'Radiology'),
    ('pharma', 'pharmacist', 'Pharmacist', '/pharmacy/dashboard', 'Pharmacy'),
    ('physio', 'physiotherapist', 'Physiotherapist',
     '/physiotherapy/dashboard', 'Patients'),
    ('dentist', 'dentist', 'Dentist', '/dentistry/dashboard', 'Dentistry'),
    ('reception', 'receptionist', 'Receptionist',
     '/reception/dashboard', 'Reception'),
    ('cashier', 'cashier', 'Cashier', '/billing/dashboard', 'Billing'),
    ('patient', 'patient', 'Patient', '/patient/dashboard', 'Patient'),
)


class PageRenderHealthTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.roles = {}
        for name in ROLES:
            role = Role(name=name)
            db.session.add(role)
            self.roles[name] = role
        db.session.commit()
        seed_permissions(db)
        self.specialty = Specialty(name='General')
        db.session.add(self.specialty)
        db.session.flush()
        for username, user_type, role_name, _, _ in ACCOUNTS:
            user = User(username=username, email=f'{username}@test.com',
                        full_name=f'Test {username}', user_type=user_type)
            user.set_password('123456')
            user.roles.append(self.roles[role_name])
            db.session.add(user)
            db.session.flush()
            if role_name == 'Doctor':
                db.session.add(Doctor(user_id=user.id,
                                      specialty_id=self.specialty.id))
            elif role_name == 'Patient':
                db.session.add(Patient(user_id=user.id))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_public_pages_render_content(self):
        for path, marker in (('/home', '<html'), ('/auth/login', 'Sign In')):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            body = response.get_data(as_text=True)
            self.assertGreater(len(body), 500, path)
            self.assertIn(marker, body, path)

    def test_each_role_landing_page_renders_content(self):
        for username, _, _, path, marker in ACCOUNTS:
            self.client.get('/auth/logout')
            response = self.client.post(
                '/auth/login',
                data={'email': f'{username}@test.com', 'password': '123456'},
                follow_redirects=True,
            )
            with self.subTest(username=username):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.request.path, path)
                body = response.get_data(as_text=True)
                self.assertGreater(len(body), 1000)
                self.assertIn('<main', body)
                self.assertIn(marker, body)
                self.assertNotIn('Internal Server Error', body)
                self.assertNotIn('Traceback (most recent call last)', body)


if __name__ == '__main__':
    unittest.main()
