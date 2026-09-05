"""Per-page SQL query profile (N+1 detector).

Logs in as a role and counts the SQL statements issued while rendering each
hot page. Anything above the budget is printed with a WARN so regressions
are visible before they reach a large database.

    python scripts/profile_queries.py
"""
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault('FLASK_CONFIG', 'development')

from sqlalchemy import event  # noqa: E402

from app import create_app, db, limiter  # noqa: E402

PAGES = [
    ('superadmin@ihis.com', [
        '/super-admin/dashboard', '/super-admin/demo', '/super-admin/system-health',
        '/clinical', '/clinical/patient/1', '/clinical/inbox', '/clinical/alerts',
        '/doctor/patients', '/doctor/dashboard', '/lab/orders', '/lab/dashboard',
        '/radiology/orders', '/radiology/dashboard', '/pharmacy/prescriptions',
        '/pharmacy/dashboard', '/pharmacy/inventory', '/nursing/dashboard',
        '/reception/appointments', '/reception/queue', '/billing/dashboard',
        '/billing/bills', '/admissions/dashboard', '/tasks/queue', '/search?q=a',
        '/super-admin/audit-logs', '/admin/staff', '/care/referrals',
    ]),
    ('dr.ahmed@ihis.com', ['/doctor/dashboard', '/doctor/patients/1', '/clinical/patient/1']),
    ('nurse@ihis.com', ['/nursing/dashboard', '/nursing/patients/1/mar']),
    ('patient@ihis.com', ['/patient/dashboard', '/patient/medical-history']),
]
BUDGET = 80
# Patient 360 aggregates ~25 independent clinical sections for one patient; its
# cost is bounded per patient (no per-row loops), so it gets a wider budget.
PAGE_BUDGET = {'/clinical/patient/1': 110}


def main():
    app = create_app('development')
    app.config['WTF_CSRF_ENABLED'] = False
    limiter.enabled = False
    import logging
    logging.getLogger('app').setLevel(logging.ERROR)
    app.logger.setLevel(logging.ERROR)
    counter = {'n': 0}

    with app.app_context():
        engine = db.engine

        @event.listens_for(engine, 'before_cursor_execute')
        def _count(conn, cursor, statement, parameters, context, executemany):
            counter['n'] += 1

        worst = []
        for email, pages in PAGES:
            client = app.test_client()
            client.post('/auth/login', data={'email': email, 'password': '123456'})
            for path in pages:
                counter['n'] = 0
                t0 = time.perf_counter()
                r = client.get(path, follow_redirects=True)
                ms = (time.perf_counter() - t0) * 1000
                budget = PAGE_BUDGET.get(path, BUDGET)
                flag = 'WARN' if counter['n'] > budget else 'ok  '
                print(f'{flag} {counter["n"]:4d} queries {ms:7.1f} ms  {r.status_code}  {path}  [{email.split("@")[0]}]')
                if counter['n'] > budget:
                    worst.append((counter['n'], path))
        print()
        if worst:
            print('Pages over budget:', worst)
            sys.exit(1)
        print('All pages within the query budget.')


if __name__ == '__main__':
    main()
