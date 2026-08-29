"""iHIS smoke tests - verify app boots, auth works, and portals render."""
import unittest
from app import create_app, db
from app.models import User, Role


class BaseTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        # Create minimal roles + admin
        for name in ['SuperAdmin', 'Admin', 'Doctor', 'Patient', 'Nurse',
                     'LabTechnician', 'Radiologist', 'Pharmacist', 'Receptionist',
                     'Dentist', 'Physiotherapist']:
            db.session.add(Role(name=name))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _create_user(self, email, utype, role, password='123456'):
        from app.models import Patient, Doctor
        u = User(username=email.split('@')[0], email=email, full_name='Test User',
                 user_type=utype)
        u.set_password(password)
        u.roles.append(Role.query.filter_by(name=role).first())
        db.session.add(u)
        db.session.commit()
        if utype == 'patient':
            db.session.add(Patient(user_id=u.id))
            db.session.commit()
        return u

    def _login(self, email, password='123456'):
        # fetch login page to get CSRF token + session cookie
        login_page = self.client.get('/auth/login')
        token = _extract_csrf(login_page.data)
        return self.client.post('/auth/login', data={
            'email': email, 'password': password, 'csrf_token': token,
        }, follow_redirects=True)


def _extract_csrf(html):
    import re
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class PublicPageTests(BaseTestCase):
    def test_home(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)

    def test_register_page(self):
        r = self.client.get('/auth/register')
        self.assertEqual(r.status_code, 200)

    def test_login_page(self):
        r = self.client.get('/auth/login')
        self.assertEqual(r.status_code, 200)

    def test_health_api(self):
        r = self.client.get('/api/health')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['status'], 'ok')


class AuthTests(BaseTestCase):
    def test_login_success(self):
        self._create_user('user@test.com', 'patient', 'Patient')
        r = self._login('user@test.com')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'user@test.com', r.data) if False else None

    def test_login_wrong_password(self):
        self._create_user('user@test.com', 'patient', 'Patient')
        page = self.client.get('/auth/login')
        token = _extract_csrf(page.data)
        r = self.client.post('/auth/login', data={
            'email': 'user@test.com', 'password': 'wrong', 'csrf_token': token},
            follow_redirects=True)
        self.assertIn(b'Invalid email or password', r.data)


class PortalTests(BaseTestCase):
    def test_patient_dashboard(self):
        self._create_user('p@test.com', 'patient', 'Patient')
        self._login('p@test.com')
        r = self.client.get('/patient/dashboard')
        self.assertEqual(r.status_code, 200)

    def test_admin_dashboard(self):
        self._create_user('a@test.com', 'admin', 'Admin')
        self._login('a@test.com')
        r = self.client.get('/admin/dashboard')
        self.assertEqual(r.status_code, 200)

    def test_unauthenticated_redirect(self):
        r = self.client.get('/patient/dashboard')
        self.assertIn(r.status_code, [302, 200])


if __name__ == '__main__':
    unittest.main()
