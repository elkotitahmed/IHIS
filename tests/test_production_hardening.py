"""Tests for new production-readiness hardening: error handlers, structured
logging/request-id middleware, and production config validation.
"""
import os
import subprocess
import sys
import unittest

from app import create_app, db

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ProductionHardeningTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_json_error_handler_for_api_404(self):
        resp = self.client.get('/api/does-not-exist')
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(resp.is_json)
        self.assertIn('error', resp.get_json())

    def test_html_not_found_page(self):
        # A browser (Accept: text/html) hitting 404 gets the HTML error page.
        resp = self.client.get('/nonexistent-page', headers={'Accept': 'text/html'})
        self.assertEqual(resp.status_code, 404)
        self.assertIn(b'Page Not Found', resp.data)

    def test_internal_error_is_generic(self):
        self.app.config['PROPAGATE_EXCEPTIONS'] = False  # let error handler run

        @self.app.route('/force-500')
        def force_500():
            raise RuntimeError('super-secret-internal-detail')

        resp = self.client.get('/force-500')
        self.assertEqual(resp.status_code, 500)
        self.assertNotIn(b'super-secret-internal-detail', resp.data)
        self.assertIn(b'Server Error', resp.data)

    def test_request_id_generated_on_context(self):
        from flask import g
        from app.services.logging import get_request_id
        with self.app.test_request_context('/'):
            rid = get_request_id()
            self.assertTrue(rid)
            self.assertEqual(rid, getattr(g, 'request_id', None))
            # Stable within a request.
            self.assertEqual(rid, get_request_id())

    def test_incoming_request_id_respected(self):
        from app.services.logging import get_request_id
        with self.app.test_request_context('/', headers={'X-Request-ID': 'abc123'}):
            self.assertEqual(get_request_id(), 'abc123')

    def test_security_headers_present_in_testing_mode(self):
        # nosniff / frame / referrer are safe in all modes; HSTS is
        # production-only and therefore intentionally absent here.
        resp = self.client.get('/')
        self.assertEqual(resp.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(resp.headers.get('X-Frame-Options'), 'SAMEORIGIN')
        self.assertEqual(resp.headers.get('Referrer-Policy'),
                         'strict-origin-when-cross-origin')
        self.assertIsNone(resp.headers.get('Strict-Transport-Security'))

    def test_private_static_uploads_blocked(self):
        # Even if legacy files existed under static/uploads, they must not be
        # served at a public URL.
        for path in ('/static/uploads/record.png',
                     '/static/ai_models/uploads/x.png',
                     '/static/ai_models/results/x.png'):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_public_static_css_still_served(self):
        self.assertEqual(self.client.get('/static/css/style.css').status_code, 200)

    def test_401_json_for_api_path(self):
        @self.app.route('/api/force-401')
        def force_401_api():
            from flask import abort
            abort(401)

        resp = self.client.get('/api/force-401',
                               headers={'Accept': 'application/json'})
        self.assertEqual(resp.status_code, 401)
        self.assertTrue(resp.is_json)
        self.assertIn('error', resp.get_json())

    def test_401_html_for_browser(self):
        @self.app.route('/force-401-html')
        def force_401_html():
            from flask import abort
            abort(401)

        resp = self.client.get('/force-401-html',
                               headers={'Accept': 'text/html'})
        self.assertEqual(resp.status_code, 401)
        self.assertIn(b'Unauthorized', resp.data)

    def test_422_json_for_api_path(self):
        @self.app.route('/api/force-422')
        def force_422_api():
            from flask import abort
            abort(422)

        resp = self.client.get('/api/force-422',
                               headers={'Accept': 'application/json'})
        self.assertEqual(resp.status_code, 422)
        self.assertTrue(resp.is_json)
        self.assertIn('error', resp.get_json())

    def test_422_html_for_browser(self):
        @self.app.route('/force-422-html')
        def force_422_html():
            from flask import abort
            abort(422)

        resp = self.client.get('/force-422-html',
                               headers={'Accept': 'text/html'})
        self.assertEqual(resp.status_code, 422)
        self.assertIn(b'Unprocessable Entity', resp.data)

    def test_all_phase14_error_handlers_registered(self):
        # Phase 14: 400,401,403,404,409,422,429,500,503 must all have handlers.
        from werkzeug.exceptions import (
            BadRequest, Unauthorized, Forbidden, NotFound, Conflict,
            UnprocessableEntity, TooManyRequests, InternalServerError,
            ServiceUnavailable,
        )
        registered = set()
        for spec in self.app.error_handler_spec.values():
            for err_map in (spec or {}).values():
                for exc in (err_map or {}):
                    registered.add(exc)
        for exc in (BadRequest, Unauthorized, Forbidden, NotFound, Conflict,
                    UnprocessableEntity, TooManyRequests,
                    InternalServerError, ServiceUnavailable):
            self.assertIn(exc, registered,
                          msg=f'error handler missing for {exc.__name__}')

    def test_production_session_cookie_security_config(self):
        # Phase 16: production session/remember cookies must be Secure+HttpOnly
        # (SameSite=Lax), with DEBUG off and strong password minimum.
        code = (
            "import os\n"
            "os.environ['FLASK_CONFIG']='production'\n"
            "os.environ['SECRET_KEY']='z'*90\n"
            "os.environ['DATABASE_URL']='postgresql+psycopg2://u:p@localhost:5432/ihis'\n"
            "from app import create_app\n"
            "app=create_app('production')\n"
            "c=app.config\n"
            "ok=(\n"
            "  c['SESSION_COOKIE_HTTPONLY'] and\n"
            "  c['SESSION_COOKIE_SECURE'] and\n"
            "  c['SESSION_COOKIE_SAMESITE']=='Lax' and\n"
            "  c['REMEMBER_COOKIE_HTTPONLY'] and\n"
            "  c['REMEMBER_COOKIE_SECURE'] and\n"
            "  c['DEBUG'] is False and\n"
            "  c['PASSWORD_MIN_LENGTH']>=12\n"
            ")\n"
            "print('COOKIE_OK' if ok else 'COOKIE_BAD')\n"
            "import sys; sys.exit(0 if ok else 1)\n"
        )
        env = dict(os.environ)
        env.update({'FLASK_CONFIG': 'production',
                    'SECRET_KEY': 'z' * 90,
                    'DATABASE_URL': 'postgresql+psycopg2://u:p@localhost:5432/ihis'})
        proc = subprocess.run([sys.executable, '-c', code], cwd=REPO_ROOT,
                              env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         msg=f'cookie/security config invalid: {proc.stdout} {proc.stderr}')

    def test_backup_script_imports_and_is_portable(self):
        # Phase 35/36: backup/backup.py must import and not use the
        # Python-3.11-only datetime.UTC (it targets 3.10+; timezone.utc is fine).
        backup_py = os.path.join(REPO_ROOT, 'backup', 'backup.py')
        with open(backup_py) as f:
            src = f.read()
        self.assertNotIn('datetime.UTC', src)
        self.assertIn('timezone.utc', src)
        ns = {'__file__': os.path.join(REPO_ROOT, 'backup', 'backup.py')}
        exec(compile(src, backup_py, 'exec'), ns)
        self.assertIn('backup_postgres', ns)
        self.assertIn('backup_sqlite', ns)
        self.assertIn('verify_backup', ns)


class RateLimitTest(unittest.TestCase):
    """Rate-limiting regression test.

    Runs in a subprocess with RATELIMIT_ENABLED=1 so the limiter is fully
    configured (hooks + fresh in-memory storage) without affecting the
    hermetic functional tests, which run with rate-limiting off.
    """
    def test_login_rate_limited(self):
        code = (
            "import os\n"
            "os.environ['RATELIMIT_ENABLED']='1'\n"
            "import re\n"
            "from app import create_app, db\n"
            "from app.models import Role, User\n"
            "app=create_app('testing')\n"
            "ctx=app.app_context(); ctx.push(); db.create_all()\n"
            "c=app.test_client()\n"
            "r=Role(name='Patient'); db.session.add(r)\n"
            "u=User(username='x',email='x@ex.com',full_name='X',user_type='patient')\n"
            "u.set_password('p'); u.roles.append(r); db.session.add(u); db.session.commit()\n"
            "g=c.get('/auth/login')\n"
            "m=re.search(r'name=\"csrf_token\"[^>]*value=\"([^\"]+)\"', g.get_data(as_text=True))\n"
            "token=m.group(1)\n"
            "statuses=[c.post('/auth/login', data={'email':'x@ex.com','password':'w','csrf_token':token}, follow_redirects=False).status_code for _ in range(11)]\n"
            "print(statuses)\n"
            "import sys\n"
            "sys.exit(0 if 429 in statuses else 1)\n"
        )
        proc = subprocess.run(
            [sys.executable, '-c', code], cwd=REPO_ROOT,
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         msg=f'rate limit not enforced: {proc.stdout} {proc.stderr}')


class ProductionConfigValidationTest(unittest.TestCase):
    def _run_with_env(self, env_extra):
        env = dict(os.environ)
        env.update(env_extra)
        code = (
            "import os\n"
            "os.environ['FLASK_CONFIG']='production'\n"
            "from app import create_app\n"
            "import sys\n"
            "try:\n"
            "    create_app('production')\n"
            "except RuntimeError:\n"
            "    print('RAISED')\n"
            "    sys.exit(0)\n"
            "print('NO_ERROR')\n"
            "sys.exit(1)\n"
        )
        proc = subprocess.run(
            [sys.executable, '-c', code], cwd=REPO_ROOT, env=env,
            capture_output=True, text=True)
        return proc

    def test_production_rejects_weak_secret(self):
        proc = self._run_with_env({'SECRET_KEY': 'change-me-to-a-long-random-string'})
        self.assertIn('RAISED', proc.stdout)

    def test_production_rejects_missing_db_url(self):
        proc = self._run_with_env({'SECRET_KEY': 'x' * 64, 'DATABASE_URL': ''})
        self.assertIn('RAISED', proc.stdout)

    def test_production_boots_with_strong_secret_and_db(self):
        proc = self._run_with_env({
            'SECRET_KEY': 'x' * 64,
            'DATABASE_URL': 'postgresql+psycopg2://u:p@localhost:5432/ihis',
        })
        self.assertIn('NO_ERROR', proc.stdout)

    def test_production_emits_hsts_header(self):
        # HSTS must be present in production (HTTPS) responses.
        code = (
            "import os\n"
            "os.environ['FLASK_CONFIG']='production'\n"
            "os.environ['SECRET_KEY']='x'*64\n"
            "os.environ['DATABASE_URL']='postgresql+psycopg2://u:p@localhost:5432/ihis'\n"
            "from app import create_app\n"
            "app=create_app('production')\n"
            "c=app.test_client()\n"
            "h=c.get('/').headers.get('Strict-Transport-Security','')\n"
            "print('HSTS='+str(h))\n"
            "import sys\n"
            "sys.exit(0 if h else 1)\n"
        )
        env = dict(os.environ)
        env.update({'FLASK_CONFIG': 'production',
                    'SECRET_KEY': 'x' * 64,
                    'DATABASE_URL': 'postgresql+psycopg2://u:p@localhost:5432/ihis'})
        proc = subprocess.run([sys.executable, '-c', code], cwd=REPO_ROOT,
                              env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         msg=f'production HSTS missing: {proc.stdout} {proc.stderr}')


if __name__ == '__main__':
    unittest.main()
