"""Container / Space deployment guards.

- Production still refuses SQLite by default, but an explicit
  ``IHIS_EPHEMERAL_DEMO=1`` allows a throw-away SQLite demo database.
- ``IHIS_BEHIND_PROXY=1`` makes url_for honour X-Forwarded-Proto (https).
- The radiology classifier falls back to the bundled package when the
  external "AI apps" checkout is absent.
"""
import os
import unittest
from unittest import mock

from app import create_app


STRONG = 'a' * 64


class DeployBootstrapTests(unittest.TestCase):
    def test_production_rejects_sqlite_without_flag(self):
        with mock.patch.dict(os.environ, {'SECRET_KEY': STRONG, 'DATABASE_URL': 'sqlite:///x.db',
                                          'IHIS_EPHEMERAL_DEMO': ''}):
            with self.assertRaises(RuntimeError):
                create_app('production')

    def test_production_allows_ephemeral_sqlite_with_flag(self):
        with mock.patch.dict(os.environ, {'SECRET_KEY': STRONG, 'DATABASE_URL': 'sqlite:///:memory:',
                                          'IHIS_EPHEMERAL_DEMO': '1'}):
            app = create_app('production')
        self.assertFalse(app.debug)
        self.assertTrue(app.config['SESSION_COOKIE_SECURE'])

    def test_proxy_fix_builds_https_urls(self):
        with mock.patch.dict(os.environ, {'SECRET_KEY': STRONG, 'DATABASE_URL': 'sqlite:///:memory:',
                                          'IHIS_EPHEMERAL_DEMO': '1', 'IHIS_BEHIND_PROXY': '1'}):
            app = create_app('production')
        with app.app_context():
            from app import db
            db.create_all()
        client = app.test_client()
        r = client.get('/auth/login', headers={'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'demo.hf.space'})
        self.assertEqual(r.status_code, 200)
        r = client.get('/', headers={'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'demo.hf.space'})
        self.assertIn(r.status_code, (200, 302))
        if r.status_code == 302:
            self.assertTrue(r.headers['Location'].startswith(('https://demo.hf.space', '/')))

    def test_bundled_radiology_package_is_usable(self):
        import config
        bundled = config.Config._RAD_BUNDLED
        for name in ('critical_results_model_negation.py', 'negation_model.pkl', 'negation_vectorizer.pkl'):
            self.assertTrue(os.path.isfile(os.path.join(bundled, name)), name)
        app = create_app('testing')
        with app.app_context():
            from app.services.radiology_critical_ai import RadiologyCriticalAI
            svc = RadiologyCriticalAI(package_dir=bundled)
            try:
                out = svc.analyze(findings='Large right pneumothorax.', impression='Pneumothorax.')
            except ImportError:      # scikit-learn/pandas not installed in this environment
                self.skipTest('classifier dependencies not installed')
            self.assertTrue(out.get('critical_finding'))

    def test_bootstrap_resolve_env_is_safe(self):
        from deployment import bootstrap
        with mock.patch.dict(os.environ, {'SECRET_KEY': '', 'DATABASE_URL': '', 'IHIS_EPHEMERAL_DEMO': '1',
                                          'FLASK_CONFIG': ''}, clear=False):
            os.environ.pop('SECRET_KEY'); os.environ.pop('DATABASE_URL'); os.environ.pop('FLASK_CONFIG')
            bootstrap.resolve_env()
            self.assertEqual(len(os.environ['SECRET_KEY']), 64)
            self.assertTrue(os.environ['DATABASE_URL'].startswith('sqlite:///'))
            self.assertEqual(os.environ['FLASK_CONFIG'], 'production')


if __name__ == '__main__':
    unittest.main()
