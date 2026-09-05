"""AI production resilience (Phases 19/20): provider failure, missing API key,
timeout, malformed response must NEVER break clinical workflows or leak PHI in
the prompt. Covered here:
  - Missing GEMINI_API_KEY -> 200 page with friendly notice (not 500)
  - Provider exception during review -> structured error, not a raise
  - Prompt contains no name / MRN / identifiers (privacy)
"""
import unittest
from unittest import mock

from app import create_app, db, bcrypt
from app.models import User, Role, Patient, Doctor


def _mk_user(username, role_name, pw='Tests@12345'):
    role = Role.query.filter_by(name=role_name).first()
    u = User(username=username, email=f'{username}@example.com',
             full_name='Test', user_type='staff',
             password_hash=bcrypt.generate_password_hash(pw).decode(),
             is_active=True)
    if role:
        u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


class AIResilienceTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for r in ('Doctor', 'Radiologist', 'Patient', 'Pharmacist', 'Admin'):
            if not Role.query.filter_by(name=r).first():
                db.session.add(Role(name=r))
        db.session.commit()
        self.client = self.app.test_client()

        # Use an Admin (user_type='admin') who has need-to-know access, so the
        # test isolates AI resilience from role-based access rules.
        self.admin = _mk_user('admin_ai', 'Admin')
        self.admin.user_type = 'admin'
        db.session.commit()

        self.patient = Patient(mrn='MRN-PRIV-0001', user_id=self.admin.id)
        db.session.add(self.patient)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, u, pw='Tests@12345'):
        self.client.post('/auth/login',
                         data={'email': u.email, 'password': pw})

    @mock.patch.dict('os.environ', {}, clear=False)
    def test_medication_review_missing_key_renders_not_500(self):
        self._login(self.admin)
        # Ensure no GEMINI_API_KEY is present.
        with mock.patch.dict('os.environ', {'GEMINI_API_KEY': ''}):
            resp = self.client.post(f'/ai/medication-review/{self.patient.id}')
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn(b'Internal Server Error', resp.data)

    def test_review_returns_error_dict_on_provider_exception(self):
        from app.services.ai.clinical_pharmacist import AIClinicalPharmacist
        ph = AIClinicalPharmacist(api_key='fake')
        with mock.patch('app.services.ai.clinical_pharmacist.'
                        'AIClinicalPharmacist._call_gemini',
                        side_effect=Exception('provider exploded')):
            result = ph.review(self.patient)
        self.assertIn('error', result)
        self.assertIn('provider exploded', result['error'])
        # returns (does not raise), so the route can render a friendly error.
        self.assertTrue(result.get('available'))

    def test_review_prompt_has_no_phi_identifiers(self):
        from app.services.ai.clinical_pharmacist import AIClinicalPharmacist
        ph = AIClinicalPharmacist(api_key='fake')
        prompt = ph._build_prompt(self.patient, [], [])
        # No name, MRN, username, or direct identifiers.
        self.assertNotIn('MRN-PRIV-0001', prompt)
        self.assertNotIn('Test', prompt)  # full_name is a direct identifier
        self.assertNotIn('admin_ai', prompt)


if __name__ == '__main__':
    unittest.main()