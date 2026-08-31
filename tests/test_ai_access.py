"""Verify role-based access control (RBAC) for the integrated AI tools.

Confirms that each healthcare-provider role can (only) reach the AI tools
relevant to its scope, and that no role has a phantom grant without a way to
exercise it.

Everything (DB creation, seeding, requests) runs inside one app context so the
in-memory SQLite DB is shared across the test client — the same approach that
works for the existing suite.
"""
import unittest

from app import create_app, db
from app.models import Role, Specialty, Doctor, User, Patient, Appointment, \
    VitalSign, Prescription
from app.permissions import seed_permissions
from seed import ROLES


# role -> the AI tools it SHOULD be able to open
EXPECTED = {
    'SuperAdmin': {'medication_review', 'fracture_detection', 'tooth_segmentation'},
    'Admin': {'medication_review', 'fracture_detection', 'tooth_segmentation'},
    'Doctor': {'medication_review', 'fracture_detection'},
    'Pharmacist': {'medication_review'},
    'Nurse': {'medication_review', 'fracture_detection', 'tooth_segmentation'},
    'Physiotherapist': {'fracture_detection'},
    'Radiologist': {'fracture_detection', 'tooth_segmentation'},
    'Dentist': {'fracture_detection', 'tooth_segmentation'},
    'LabTechnician': set(),
    'Receptionist': set(),
    'Patient': set(),
}

TOOL_NAMES = ('medication_review', 'fracture_detection', 'tooth_segmentation')

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


class AIAccessTest(unittest.TestCase):
    def test_role_access_matrix(self):
        from datetime import datetime
        app = create_app('testing')
        app.config['WTF_CSRF_ENABLED'] = False
        with app.app_context():
            db.create_all()
            for name in ROLES:
                db.session.add(Role(name=name))
            db.session.commit()
            seed_permissions(db)

            doctor_profile = None
            patient = None
            nurse_user = None
            pharma_user = None
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
                    doctor_profile = Doctor(user_id=u.id, specialty_id=spec.id)
                    db.session.add(doctor_profile)
                elif utype == 'patient':
                    patient = Patient(user_id=u.id)
                    db.session.add(patient)
                if uname == 'nurse':
                    nurse_user = u
                if uname == 'pharma':
                    pharma_user = u
                db.session.flush()
            db.session.commit()

            # Documented relationships so clinical roles pass need-to-know.
            if doctor_profile is not None:
                db.session.add(Appointment(patient_id=patient.id,
                                           doctor_id=doctor_profile.id,
                                           scheduled_at=datetime.now()))
            if nurse_user is not None:
                db.session.add(VitalSign(patient_id=patient.id,
                                         nurse_id=nurse_user.id))
            if pharma_user is not None:
                db.session.add(Prescription(patient_id=patient.id, status='Active'))
            db.session.commit()

            patient_id = patient.id
            tool_paths = {
                'medication_review': f'/ai/medication-review/{patient_id}',
                'fracture_detection': '/ai/fracture-detection',
                'tooth_segmentation': '/ai/tooth-segmentation',
            }

            client = app.test_client()
            for uname, (utype, roles) in ACCOUNTS.items():
                email = f'{uname}@test.com'
                allowed = set()
                for r in roles:
                    allowed |= EXPECTED.get(r, set())
                resp = client.post('/auth/login', data={
                    'email': email, 'password': '123456',
                }, follow_redirects=True)
                self.assertEqual(resp.status_code, 200, f'login failed for {email}')
                for tool in TOOL_NAMES:
                    resp = client.get(tool_paths[tool])
                    ctx = f'[{email}] tool={tool}'
                    if tool in allowed:
                        self.assertEqual(resp.status_code, 200,
                                         f'{ctx} should be ALLOWED but got {resp.status_code}')
                    else:
                        self.assertIn(resp.status_code, (302, 403, 404),
                                      f'{ctx} should be DENIED but got {resp.status_code}')
                client.get('/auth/logout')


if __name__ == '__main__':
    unittest.main()
