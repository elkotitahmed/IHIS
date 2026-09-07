"""Print axe-core violation details (rule, impact, element html) for a list of
pages, signed in as a role. Starts its own dev server with AI disabled.

    python scripts/a11y_dump.py physician /clinical/patient/1 /doctor/patients/1/emr/add
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tests', 'e2e'))
ACCOUNTS = {'physician': 'dr.ahmed@ihis.com', 'patient': 'patient@ihis.com', 'pharmacist': 'pharma@ihis.com',
            'nurse': 'nurse@ihis.com', 'superadmin': 'superadmin@ihis.com', 'radiologist': 'radio@ihis.com'}


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:  # noqa: BLE001
        pass
    role, pages = sys.argv[1], sys.argv[2:]
    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, PORT=str(port), FLASK_CONFIG='development', AI_ENABLED='0', PYTHONIOENCODING='utf-8')
    proc = subprocess.Popen([sys.executable, '-c', f"import run; from app import limiter; limiter.enabled = False; run.app.run(port={port}, debug=False, use_reloader=False)"],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}'
    for _ in range(120):
        try:
            if urllib.request.urlopen(url + '/health/live', timeout=1).status == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    try:
        from axe_playwright_python.sync_playwright import Axe
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(); page = b.new_page()
            page.goto(url + '/auth/login'); page.fill('input[type=email]', ACCOUNTS[role]); page.fill('input[type=password]', '123456')
            page.click('button[type=submit], input[type=submit]'); page.wait_for_load_state('networkidle')
            for path in pages:
                page.goto(url + path); page.wait_for_load_state('networkidle')
                res = Axe().run(page, options={'runOnly': ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']})
                print(f'\n===== {path}')
                for v in res.response.get('violations', []):
                    print(f"[{v['impact']}] {v['id']} — {v['help']} ({len(v['nodes'])})")
                    for n in v['nodes'][:6 if v['id'] != 'color-contrast' else 12]:
                        msg = (n.get('any') or n.get('all') or [{}])[0].get('message', '')
                        print('    ', n['html'][:150].replace('\n', ' '), '|', msg[:120])
            b.close()
    finally:
        proc.kill()


if __name__ == '__main__':
    main()
