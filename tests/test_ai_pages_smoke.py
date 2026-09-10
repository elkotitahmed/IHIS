"""Every zero-argument AI page returns 200 for the roles that should reach it,
and is denied (302/403/404) for everyone else.

Complements ``test_ai_access.py`` (which checks the tools that need a
patient/order argument). This one walks the https:// sidebar "AI TOOLS" list,
so breaking any page render or role rule fails fast.

Everything (DB creation, seeding, requests) runs inside one app context so the
in-memory SQLite DB is shared across the test client.
"""
import unittest

from app import create_app, db
from app.models import Role, Specialty, Doctor, User, Patient, Prescription
from app.permissions import seed_permissions
from seed import ROLES


# route -> roles that are granted access by the route decorators
ZERO_ARG_ROUTES = {
    '/ai/hub': {'SuperAdmin', 'Admin', 'Doctor', 'Pharmacist', 'Nurse', 'Physiotherapist',
                'Radiologist', 'Dentist', 'LabTechnician', 'Receptionist', 'Patient'},
    '/ai/chest-xray': {'Radiologist', 'Doctor', 'Admin', 'SuperAdmin'},
    '/ai/fracture-detection': {'Radiologist', 'Doctor', 'Nurse',
                               'Physiotherapist', 'Dentist', 'Admin',
                               'SuperAdmin'},
    '/ai/tooth-segmentation': {'Dentist', 'Radiologist', 'Nurse', 'Admin',
                               'SuperAdmin'},
    '/ai/skin-lesion-detection': {'Doctor', 'Dentist', 'Nurse', 'Admin',
                                  'SuperAdmin'},
    '/ai/health-insights': {'Patient', 'Admin', 'SuperAdmin'},
    '/pharmacy/ai-workbench': {'Pharmacist', 'Admin', 'SuperAdmin'},
    '/admin/capacity': {'Admin', 'SuperAdmin'},
}

# username -> (user_type, list of roles)
ACCOUNTS = {
    'superadmin': ('admin', ['SuperAdmin', 'Admin']),
    'admin': ('admin', ['Admin']),
    'doctor': ('doctor', ['Doctor']),
    'pharma': ('pharmacist', ['Pharmacist']),
    'nurse': ('nurse', ['Nurse']),
    'physio': ('physiotherapist', ['Physiotherapist']),
    'radio': ('radiologist', ['Radiologist']),
    'dentist': ('dentist', ['Dentist']),
    'lab': ('lab_technician', ['LabTechnician']),
    'reception': ('receptionist', ['Receptionist']),
    'patient': ('patient', ['Patient']),
}

DENIED_STATUS = (302, 403, 404)


class AIPagesSmokeTest(unittest.TestCase):
    def test_zero_arg_ai_pages_per_role(self):
        app = create_app('testing')
        app.config['WTF_CSRF_ENABLED'] = False
        with app.app_context():
            db.create_all()
            for name in ROLES:
                db.session.add(Role(name=name))
            db.session.commit()
            seed_permissions(db)

            roles_by_name = {r.name: r for r in Role.query.all()}
            for uname, (utype, roles) in ACCOUNTS.items():
                u = User(username=uname, email=f'{uname}@test.com',
                         full_name=f'Test {uname}', user_type=utype)
                u.set_password('123456')
                with db.session.no_autoflush:
                    for r in roles:
                        u.roles.append(roles_by_name[r])
                db.session.add(u)
                db.session.flush()
                if utype == 'doctor':
                    spec = Specialty(name='General')
                    db.session.add(spec)
                    db.session.flush()
                    db.session.add(Doctor(user_id=u.id, specialty_id=spec.id))
                elif utype == 'patient':
                    db.session.add(Patient(user_id=u.id))
                db.session.flush()
            db.session.commit()

            # one active Rx so the pharmacy AI workbench has rows to show
            pat = Patient.query.first()
            db.session.add(Prescription(patient_id=pat.id,
                                        status='Active'))
            db.session.commit()

            client = app.test_client()
            failures = []
            checked = 0
            for uname, (utype, roles) in ACCOUNTS.items():
                email = f'{uname}@test.com'
                allowed = set()
                for r in roles:
                    for route, aroles in ZERO_ARG_ROUTES.items():
                        if r in aroles:
                            allowed.add(route)
                resp = client.post('/auth/login', data={
                    'email': email, 'password': '123456',
                }, follow_redirects=True)
                self.assertEqual(resp.status_code, 200,
                                 f'login failed for {email}')
                for route in ZERO_ARG_ROUTES:
                    resp = client.get(route)
                    checked += 1
                    ctx = f'[{email}] {route}'
                    if route in allowed:
                        if resp.status_code != 200:
                            failures.append(
                                f'{ctx} should be ALLOWED, got {resp.status_code}')
                    else:
                        if resp.status_code not in DENIED_STATUS:
                            failures.append(
                                f'{ctx} should be DENIED, got {resp.status_code}')
                client.get('/auth/logout')

            self.assertEqual(failures, [], f'{len(failures)} failures '
                                          f'(checked {checked} requests):\n'
                                          + '\n'.join(failures))


if __name__ == '__main__':
    unittest.main()