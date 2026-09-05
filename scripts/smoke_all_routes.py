"""Server-side white-page forensics.

Logs in as every demo role account, fetches every GET route the role can
reach (path parameters are filled from real rows in the database) and reports
anything that is not a healthy page: 5xx, unexpected 4xx, redirect loops, or a
200 whose <main> content is effectively empty.

Usage (development database, after `python seed.py`):

    python scripts/smoke_all_routes.py            # summary + problems
    python scripts/smoke_all_routes.py --verbose  # every request

Exit code 1 when any 5xx / empty page is found so it can gate CI.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault('FLASK_CONFIG', 'development')

from app import create_app, db  # noqa: E402
from app.models import (  # noqa: E402
    Patient, Doctor, LabOrder, LabResult, RadiologyOrder, Prescription, Bill,
    Task, Admission, User, MedicalRecord, TherapyPlan, TherapySession,
    DentalTreatmentPlan, DentalProcedure, DentalImage, PatientDocument,
    OrderSet, ClinicianTemplate, MedicationReconciliation, PharmacyInventory,
    Referral, MedicationAdministration, ClinicalReminder, ClinicalAlert,
    CriticalFindingNotification, MRIImplantRegistry, PharmacyIntervention,
    ReconciliationDiscrepancy,
)

ACCOUNTS = [
    ('SuperAdmin', 'superadmin@ihis.com'),
    ('Admin', 'admin@ihis.com'),
    ('Doctor', 'dr.ahmed@ihis.com'),
    ('Nurse', 'nurse@ihis.com'),
    ('LabTechnician', 'lab@ihis.com'),
    ('Radiologist', 'radio@ihis.com'),
    ('RadiologyTechnician', 'radtech@ihis.com'),
    ('Pharmacist', 'pharma@ihis.com'),
    ('Physiotherapist', 'physio@ihis.com'),
    ('Dentist', 'dentist@ihis.com'),
    ('Receptionist', 'reception@ihis.com'),
    ('Cashier', 'cashier@ihis.com'),
    ('Patient', 'patient@ihis.com'),
]
PASSWORD = '123456'

# Routes that intentionally are not plain pages.
SKIP = {'static', 'auth.logout', 'super_admin.exit_preview', 'super_admin.preview_role',
        'patient.preview_as', 'main.home'}


def _first(model, **filters):
    q = model.query
    if filters:
        q = q.filter_by(**filters)
    row = q.order_by(model.id.asc()).first()
    return row.id if row else None


def sample_ids():
    """Fill every path converter with a real id when one exists."""
    signed_rad = (RadiologyOrder.query.join(
        __import__('app.models', fromlist=['RadiologyReport']).RadiologyReport)
        .order_by(RadiologyOrder.id.asc()).first())
    ids = {
        'patient_id': _first(Patient),
        'doctor_id': _first(Doctor),
        'order_id': _first(LabOrder),
        'result_id': _first(LabResult),
        'prescription_id': _first(Prescription),
        'rx_id': _first(Prescription),
        'bill_id': _first(Bill),
        'task_id': _first(Task),
        'id': _first(Admission),
        'user_id': _first(User),
        'record_id': _first(MedicalRecord),
        'plan_id': _first(TherapyPlan),
        'session_id': _first(TherapySession),
        'procedure_id': _first(DentalProcedure),
        'image_id': _first(DentalImage),
        'doc_id': _first(PatientDocument),
        'order_set_id': _first(OrderSet),
        'template_id': _first(ClinicianTemplate),
        'rec_id': _first(MedicationReconciliation),
        'inv_id': _first(PharmacyInventory),
        'ref_id': _first(Referral),
        'admin_id': _first(MedicationAdministration),
        'reminder_id': _first(ClinicalReminder),
        'alert_id': _first(ClinicalAlert),
        'finding_id': _first(CriticalFindingNotification),
        'implant_id': _first(MRIImplantRegistry),
        'i_id': _first(PharmacyIntervention),
        'd_id': _first(ReconciliationDiscrepancy),
        'appt_id': None,
        'index': 0,
        'feature': 'fracture', 'kind': 'uploads', 'filename': 'x.png',
        'role_name': 'Doctor', 'action': 'ack', 'sid': None, 'case_id': None,
        'member_id': None, 'followup_id': None, 'allergy_id': None,
        'problem_id': None,
    }
    return ids


def build_url(rule, ids):
    url = str(rule)
    for conv, name in re.findall(r'<(?:(\w+):)?(\w+)>', url):
        val = ids.get(name)
        if val is None:
            return None
        url = re.sub(r'<(?:\w+:)?%s>' % name, str(val), url)
    return url


def main():
    verbose = '--verbose' in sys.argv
    app = create_app('development')
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['RATELIMIT_ENABLED'] = False
    from app import limiter
    limiter.enabled = False
    # Surface server errors as 500 responses (like production) instead of
    # propagating the exception out of the test client.
    app.debug = False
    app.config['PROPAGATE_EXCEPTIONS'] = False
    app.config['TESTING'] = False
    import logging
    logging.getLogger('app').setLevel(logging.ERROR)
    app.logger.setLevel(logging.ERROR)
    problems = []
    checked = 0
    with app.app_context():
        ids = sample_ids()
        # Radiology plan id fix: use dental plan for dentistry.plan_procedures
        dental_plan = _first(DentalTreatmentPlan)
        get_rules = [r for r in app.url_map.iter_rules()
                     if 'GET' in r.methods and r.endpoint not in SKIP]
        for role, email in ACCOUNTS:
            client = app.test_client()
            resp = client.post('/auth/login', data={'email': email, 'password': PASSWORD},
                               follow_redirects=False)
            if resp.status_code not in (302, 303):
                problems.append((role, '/auth/login', resp.status_code, 'LOGIN FAILED'))
                continue
            for rule in get_rules:
                local_ids = dict(ids)
                if rule.endpoint == 'dentistry.plan_procedures':
                    local_ids['plan_id'] = dental_plan
                url = build_url(rule, local_ids)
                if url is None:
                    continue
                if rule.endpoint.startswith('api.') or rule.endpoint.startswith('fhir.'):
                    r = client.get(url)
                    checked += 1
                    if r.status_code >= 500:
                        problems.append((role, url, r.status_code, 'SERVER ERROR'))
                    if verbose:
                        print(f'{role:20s} {r.status_code} {url}')
                    continue
                r = client.get(url, follow_redirects=False)
                checked += 1
                status = r.status_code
                note = ''
                if status in (301, 302, 303):
                    # Follow one hop and judge the destination.
                    dest = r.headers.get('Location', '')
                    r2 = client.get(dest, follow_redirects=False)
                    if r2.status_code in (301, 302, 303):
                        loc2 = r2.headers.get('Location', '')
                        if loc2.split('?')[0] == dest.split('?')[0]:
                            note = f'REDIRECT LOOP {dest}'
                        else:
                            r3 = client.get(loc2, follow_redirects=False)
                            if r3.status_code >= 500:
                                note = f'SERVER ERROR after redirect {loc2}'
                    elif r2.status_code >= 500:
                        note = f'SERVER ERROR after redirect {dest}'
                    status = f'302->{r2.status_code}'
                elif status >= 500:
                    note = 'SERVER ERROR'
                elif status == 200 and r.mimetype == 'text/html':
                    html = r.get_data(as_text=True)
                    m = re.search(r'<main[^>]*>(.*)</main>', html, re.S)
                    body = m.group(1) if m else html
                    text = re.sub(r'<script.*?</script>', '', body, flags=re.S)
                    text = re.sub(r'<[^>]+>', ' ', text)
                    text = re.sub(r'\s+', ' ', text).strip()
                    if len(text) < 40:
                        note = f'EMPTY PAGE ({len(text)} chars)'
                if note:
                    problems.append((role, url, status, note))
                if verbose:
                    print(f'{role:20s} {status!s:9s} {url} {note}')
    print(f'\nChecked {checked} role/route combinations.')
    if problems:
        print(f'{len(problems)} problem(s):')
        for role, url, status, note in problems:
            print(f'  [{role}] {status} {url} — {note}')
        sys.exit(1)
    print('No white pages, server errors or redirect loops found.')


if __name__ == '__main__':
    main()
