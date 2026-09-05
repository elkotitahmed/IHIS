"""Regression tests: AI skin lesion detection (ResNet-50 + EfficientNet-B0).

Covers RBAC reachability, graceful fallback when the model is missing, the
private-media rule for the new ``skin`` feature, and the authenticated serving
of generated Grad-CAM heatmaps.
"""
import io
import os
import unittest
from unittest import mock

from app import create_app, db, bcrypt
from app.models import User, Role

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


class SkinLesionTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for name in ('Doctor', 'Radiologist', 'Dentist', 'Nurse', 'Patient',
                     'Admin', 'SuperAdmin'):
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
        return self.client.post('/auth/login', data={
            'email': u.email, 'password': password})

    def test_requires_login(self):
        resp = self.client.get('/ai/skin-lesion-detection')
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_doctor_can_open_page(self):
        uid, pw = make_user('doc_skin', 'Doctor')
        self._login('doc_skin', pw)
        resp = self.client.get('/ai/skin-lesion-detection')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('AI Skin Lesion Detection'.encode(), resp.data)

    def test_graceful_when_model_missing(self):
        with mock.patch('app.services.ai.skin_lesion_classification.'
                        'skin_model_available', return_value=False):
            uid, pw = make_user('doc_skin', 'Doctor')
            self._login('doc_skin', pw)
            resp = self.client.post('/ai/skin-lesion-detection', data={
                'file': (io.BytesIO(TEST_IMG), 'lesion.jpg')},
                content_type='multipart/form-data')
            self.assertEqual(resp.status_code, 200)
            # Should render a friendly message, never a public static URL.
            self.assertNotIn(b'/static/ai_models', resp.data)

    @mock.patch('app.services.ai.skin_lesion_classification.'
                'classify_skin_lesion',
                return_value={
                    'feature': 'skin',
                    'orig_key': 'abc.jpg',
                    'heatmap_key': None,
                    'prediction': 'Melanoma',
                    'is_melanoma': True,
                    'percent': 74,
                    'confidence': 0.74,
                    'proba_melanoma': 0.74,
                    'proba_nevus': 0.26,
                    'per_model': [
                        {'model': 'Resnet50', 'prediction': 'Nevus',
                         'proba_nevus': 0.51, 'proba_melanoma': 0.49,
                         'confidence': 0.51},
                    ],
                    'models_used': ['resnet50_best.pth'],
                })
    def test_post_renders_result(self, _):
        uid, pw = make_user('doc_skin', 'Doctor')
        self._login('doc_skin', pw)
        resp = self.client.post('/ai/skin-lesion-detection', data={
            'file': (io.BytesIO(TEST_IMG), 'lesion.jpg')},
            content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Melanoma', resp.data)

    def test_skin_media_served_to_authorized_role(self):
        upload_dir = os.path.join(self.app.config['UPLOAD_FOLDER'],
                                  'ai', 'skin', 'results')
        os.makedirs(upload_dir, exist_ok=True)
        target = os.path.join(upload_dir, 'abc.jpg')
        with open(target, 'wb') as f:
            f.write(TEST_IMG)
        uid, pw = make_user('rad_skin', 'Doctor')
        self._login('rad_skin', pw)
        resp = self.client.get('/ai/media/skin/results/abc.jpg')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, TEST_IMG)

        # Same file must still be private at a public URL.
        blocked = self.client.get('/static/ai_models/results/abc.jpg')
        self.assertEqual(blocked.status_code, 404)

        # Unauthorized role cannot fetch it.
        uid2, pw2 = make_user('pat_skin', 'Patient')
        self.client.get('/auth/logout')
        self._login('pat_skin', pw2)
        denied = self.client.get('/ai/media/skin/results/abc.jpg')
        self.assertIn(denied.status_code, (302, 403))

    def test_invalid_feature_blocked(self):
        uid, pw = make_user('doc_skin', 'Doctor')
        self._login('doc_skin', pw)
        resp = self.client.get('/ai/media/skinfoo/uploads/abc.jpg')
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()