"""WCAG 2.1 AA scan (axe-core) of the key screens in English and Arabic.

Fails on any *serious* or *critical* violation. Minor/moderate findings are
printed so they can be worked down without blocking.
"""
import pytest
from axe_playwright_python.sync_playwright import Axe

PAGES = ['/auth/login', '/doctor/dashboard', '/ai/hub', '/clinical/patient/1', '/clinical/inbox',
         '/doctor/patients/1/emr/add', '/ai/chest-xray']
PATIENT_PAGES = ['/patient/dashboard', '/patient/health-summary']
PHARMACY_PAGES = ['/pharmacy/prescriptions', '/pharmacy/drug-check']


def _scan(page, url):
    page.goto(url)
    page.wait_for_load_state('networkidle')
    res = Axe().run(page, options={'runOnly': ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']})
    violations = res.response.get('violations', [])
    bad = [v for v in violations if v.get('impact') in ('serious', 'critical')]
    for v in violations:
        print(f"[{v.get('impact')}] {url}: {v['id']} — {v['help']} ({len(v['nodes'])} nodes)")
    return bad


@pytest.mark.e2e
@pytest.mark.parametrize('lang', ['en', 'ar'])
def test_staff_pages_have_no_serious_violations(login, server_url, lang):
    page = login('physician')
    problems = []
    for path in PAGES:
        problems += _scan(page, f'{server_url}{path}?lang={lang}')
    assert not problems, '\n'.join(f"{v['id']}: {v['help']} -> {[n['target'] for n in v['nodes'][:3]]}" for v in problems)


@pytest.mark.e2e
def test_patient_and_pharmacy_pages_have_no_serious_violations(login, server_url):
    page = login('patient')
    problems = []
    for path in PATIENT_PAGES:
        problems += _scan(page, server_url + path)
    page = login('pharmacist')
    for path in PHARMACY_PAGES:
        problems += _scan(page, server_url + path)
    assert not problems, '\n'.join(f"{v['id']}: {v['help']} -> {[n['target'] for n in v['nodes'][:3]]}" for v in problems)
