"""Real-browser journeys (Playwright + Chromium) against a live iHIS server.

Opt-in: set ``IHIS_E2E=1`` (the normal ``pytest tests`` run skips this folder).
The server is the development app on the seeded demo database, started on a
free port with ``AI_ENABLED=0`` so no provider quota is ever used.

    IHIS_E2E=1 python -m pytest tests/e2e -q
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PASSWORD = '123456'
ACCOUNTS = {'physician': 'dr.ahmed@ihis.com', 'patient': 'patient@ihis.com', 'pharmacist': 'pharma@ihis.com',
            'nurse': 'nurse@ihis.com', 'superadmin': 'superadmin@ihis.com', 'radiologist': 'radio@ihis.com'}


def pytest_ignore_collect(collection_path, config):
    return os.environ.get('IHIS_E2E') != '1'


def _free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope='session')
def server_url():
    port = _free_port()
    env = dict(os.environ, PORT=str(port), FLASK_CONFIG='development', AI_ENABLED='0', PYTHONIOENCODING='utf-8')
    # rate limiting off: the journeys sign in many times in a minute
    proc = subprocess.Popen([sys.executable, '-c',
                             f"import run; from app import limiter; limiter.enabled = False; "
                             f"run.app.run(port={port}, debug=False, use_reloader=False)"],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}'
    for _ in range(120):
        try:
            if urllib.request.urlopen(url + '/health/live', timeout=1).status == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    else:
        proc.kill(); raise RuntimeError('iHIS server did not start')
    yield url
    proc.kill()


@pytest.fixture
def login(page, server_url):
    def _login(role):
        page.goto(server_url + '/auth/logout')          # a previous role may still be signed in
        page.goto(server_url + '/auth/login')
        page.fill('input[type=email]', ACCOUNTS[role])
        page.fill('input[type=password]', PASSWORD)
        page.click('button[type=submit], input[type=submit]')
        page.wait_for_load_state('networkidle')
        return page
    return _login
