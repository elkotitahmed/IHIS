"""Regression tests: private AI-generated medical images must never be exposed
at public /static/... URLs and must be served only via an authenticated,
authorization-gated endpoint (see release Phases 8/9 and ai.py `ai_media`)."""
import io
import os
import unittest
from unittest import mock

from app import create_app, db
from app.models import User, Role
from app import bcrypt

WEAK = 'testing-secret-not-used'
TEST_IMG = (b'\xff\xd8\xff\xe0' + b'\x00' * 256)  # fake JPEG header


def make_user(username, role_name, password='Tests@12345'):
    role = Role.query.filter_by(name=role_name).first()
    u = User(
        username=username,
        email=f'{username}@example.com',
        full_name='Test User',
        user_type='staff',
        password_hash=bcrypt.generate_password_hash(password).decode('utf-8'),
        is_active=True,
    )
    if role:
        u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u.id, password


class AIMediaPrivacyTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        for name in ('Doctor', 'Radiologist', 'Dentist', 'Patient'):
            if not Role.query.filter_by(name=name).first():
                db.session.add(Role(name=name))
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, username, password):
        u = User.query.filter_by(username=username).first()
        # Do NOT follow redirects: an unauthorised/incomplete role may loop at
        # the per-role dashboard. Just return the login POST response (302 on
        # success, 200 re-render on failure).
        return self.client.post('/auth/login', data={
            'email': u.email, 'password': password})

    def test_public_static_private_paths_blocked(self):
        # Even when files exist under /static/uploads or /static/ai_models, the
        # app returns 404 rather than serving PHI publicly.
        for path in ('/static/uploads/abc.png',
                     '/static/ai_models/uploads/abc.png',
                     '/static/ai_models/results/abc.png'):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 404, path)

    def test_public_static_css_still_served(self):
        resp = self.client.get('/static/css/style.css')
        self.assertEqual(resp.status_code, 200)

    def test_ai_media_requires_login(self):
        resp = self.client.get('/ai/media/fracture/uploads/whatever.png')
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_ai_media_denied_to_patient_role(self):
        uid, pw = make_user('pat_ai', 'Patient')
        self._login('pat_ai', pw)
        resp = self.client.get('/ai/media/fracture/uploads/whatever.png')
        self.assertIn(resp.status_code, (302, 403))

    def test_ai_media_path_traversal_blocked(self):
        uid, pw = make_user('rad_ai', 'Radiologist')
        self._login('rad_ai', pw)
        resp = self.client.get('/ai/media/fracture/uploads/..%2f..%2fsecret.png')
        self.assertIn(resp.status_code, (302, 403, 404))

    @mock.patch('app.services.ai.tooth_segmentation.tooth_model_available',
                return_value=False)
    def test_tooth_run_graceful_when_model_missing(self, _):
        uid, pw = make_user('dent_ai', 'Dentist')
        self._login('dent_ai', pw)
        resp = self.client.post('/ai/tooth-segmentation', data={
            'file': (io.BytesIO(b'not an image'), 'pan.jpg')},
            content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 200)
        # Should render the page (with a friendly error), not a public URL.
        self.assertNotIn(b'/static/ai_models', resp.data)

    def test_ai_media_serves_private_file_to_authorized_role(self):
        # Simulate a stored private fracture result and verify the authorized
        # role can fetch it through the protected endpoint.
        upload_dir = os.path.join(self.app.config['UPLOAD_FOLDER'],
                                  'ai', 'fracture', 'results')
        os.makedirs(upload_dir, exist_ok=True)
        target = os.path.join(upload_dir, 'abc123.jpg')
        with open(target, 'wb') as f:
            f.write(TEST_IMG)

        uid, pw = make_user('rad_ai', 'Radiologist')
        self._login('rad_ai', pw)
        resp = self.client.get('/ai/media/fracture/results/abc123.jpg')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, TEST_IMG)

        # The static route for the same name is still blocked.
        blocked = self.client.get('/static/ai_models/results/abc123.jpg')
        self.assertEqual(blocked.status_code, 404)

        # And an unauthorized user cannot fetch it.
        uid2, pw2 = make_user('pat_ai', 'Patient')
        self.client.get('/auth/logout')
        self._login('pat_ai', pw2)
        denied = self.client.get('/ai/media/fracture/results/abc123.jpg')
        self.assertIn(denied.status_code, (302, 403))


if __name__ == '__main__':
    unittest.main()