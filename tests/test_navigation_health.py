"""Navigation regression checks for visible role menus."""
import unittest

from app import create_app, db
from app.models import Role, User
from app.permissions import seed_permissions
from seed import ROLES


class NavigationHealthTest(unittest.TestCase):
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
        self.user = User(username='cashier', email='cashier@test.com',
                         full_name='Test Cashier', user_type='cashier')
        self.user.set_password('123456')
        self.user.roles.append(self.roles['Cashier'])
        db.session.add(self.user)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_cashier_menu_is_unique_and_rendered(self):
        response = self.client.post(
            '/auth/login',
            data={'email': 'cashier@test.com', 'password': '123456'},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('Billing', body)
        self.assertIn('/billing/dashboard', body)
        self.assertEqual(body.count('/billing/dashboard'), 1)

    def test_cashier_cannot_open_clinical_admin_pages(self):
        self.client.post('/auth/login', data={
            'email': 'cashier@test.com', 'password': '123456'})
        for path in ('/admin/dashboard', '/doctor/patients',
                     '/clinical/patient/1/allergies/1/edit'):
            response = self.client.get(path)
            with self.subTest(path=path):
                self.assertIn(response.status_code, (403, 404, 405))

    def test_nurse_menu_hides_permissioned_clinical_links(self):
        nurse = User(username='nurse', email='nurse@test.com',
                     full_name='Test Nurse', user_type='nurse')
        nurse.set_password('123456')
        nurse.roles.append(self.roles['Nurse'])
        db.session.add(nurse)
        db.session.commit()

        self.client.post('/auth/login', data={
            'email': 'nurse@test.com', 'password': '123456'})
        body = self.client.get('/nursing/dashboard').get_data(as_text=True)

        self.assertIn('/clinical', body)
        self.assertIn('/clinical/alerts', body)
        for path in ('/clinical/inbox', '/clinical/reminders',
                     '/clinical/recall-board', '/clinical/order-sets',
                     '/clinical/templates'):
            with self.subTest(path=path):
                self.assertNotIn(f'href="{path}"', body)

    def test_non_patient_documents_request_does_not_raise(self):
        admin = User(username='admin', email='admin@test.com',
                     full_name='Test Admin', user_type='admin')
        admin.set_password('123456')
        admin.roles.append(self.roles['Admin'])
        db.session.add(admin)
        db.session.commit()

        self.client.post('/auth/login', data={
            'email': 'admin@test.com', 'password': '123456'})
        response = self.client.get('/patient/documents')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/dashboard'))


if __name__ == '__main__':
    unittest.main()
