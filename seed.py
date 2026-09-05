"""Seed data for iHIS - creates roles, departments, specialties, imaging types,
test catalog, medications, and demo users for every portal.

Production usage:
    python seed.py --roles-only    # safe: creates roles + permissions only
    python seed.py --roles         # same as --roles-only

Development usage:
    python seed.py                 # full demo seed (roles + demo data + users)
    python seed.py --no-demo       # roles + catalog only, no demo users/records

NEVER run full seed in production. Use --roles-only.
"""
import sys
from datetime import datetime, timedelta
from app import create_app, db
from app.permissions import seed_permissions
from app.utils import assign_mrn
from app.models import (
    User, Role, Permission, Department, Specialty, Doctor, Patient,
    ImagingType, LabTestCatalog, Medication, PharmacyInventory,
    DrugInteraction,
    DentalSpecialty, Dentist, PhysicalTherapist, SystemSetting,
    MedicalRecord, Diagnosis, Prescription, PrescriptionItem, LabOrder, LabResult,
    RadiologyOrder, RadiologyReport, Referral, CareTeam, CareTeamMember,
    MultidisciplinaryCase, Appointment, VitalSign, NursingNote, CarePlan,
    DentalRecord, DentalChart, TherapyAssessment, TherapyPlan,
    Notification, PatientDocument, ServiceCatalog, Bill, BillItem, Payment,
    Ward, Bed, Admission,
    TimelineEvent, ClinicalAlert, Allergy, Problem, ImmunizationRecord, FollowUp,
    NursingRiskAssessment, MedicationReconciliation, ReconciliationDiscrepancy,
    PharmacyIntervention,
    OrderSet, OrderSetItem, ClinicianTemplate, ClinicalReminder,
)

ROLES = {
    'SuperAdmin': 'Full system control',
    'Admin': 'Hospital administration',
    'Doctor': 'Clinical care and EMR',
    'Patient': 'Patient portal access',
    'Nurse': 'Nursing and vitals',
    'LabTechnician': 'Laboratory operations',
    'Radiologist': 'Radiology reporting and sign-off',
    'RadiologyTechnician': 'Radiology study acquisition (no reporting)',
    'Pharmacist': 'Pharmacy operations',
    'Receptionist': 'Appointment and reception',
    'Dentist': 'Dental care',
    'Physiotherapist': 'Physical therapy and rehab',
    'Cashier': 'Billing and payments',
}

DEPARTMENTS = ['Emergency', 'Internal Medicine', 'Cardiology', 'Surgery', 'Radiology',
               'Pathology', 'Nursing', 'Pharmacy', 'Reception', 'Dentistry',
               'Rehabilitation', 'Administration', 'Dermatology']

SPECIALTIES = [
    'Internal Medicine', 'Cardiology', 'Neurology', 'Pediatrics', 'Orthopedics',
    'Surgery', 'ENT', 'Dermatology', 'Psychiatry', 'Ophthalmology', 'Oncology',
    'Gynecology', 'Urology', 'Endocrinology', 'Gastroenterology', 'Pulmonology',
    'Nephrology', 'Family Medicine', 'Emergency Medicine',
]

DENTAL_SPECIALTIES = [
    'General Dentistry', 'Orthodontics', 'Prosthodontics', 'Endodontics',
    'Periodontics', 'Oral Surgery', 'Pediatric Dentistry', 'Cosmetic Dentistry',
    'Implantology', 'Oral Medicine', 'Maxillofacial Surgery',
]

IMAGING_TYPES = [
    ('X-Ray', 'Radiography imaging', 120.0), ('CT Scan', 'Computed tomography', 800.0),
    ('MRI', 'Magnetic resonance imaging', 1500.0), ('Ultrasound', 'Sonography', 400.0),
    ('Mammography', 'Breast imaging', 500.0), ('PET Scan', 'Positron emission tomography', 4000.0),
    ('Echocardiography', 'Cardiac ultrasound', 600.0),
]

LAB_TESTS = [
    # (name, category, normal_range, unit, price, (critical_low, critical_high, notes)?)
    ('Complete Blood Count (CBC)', 'Hematology', '4.0-11.0', 'x10^9/L', 100.0,
     (0.5, 50.0, 'Panic WBC count: repeat and notify the treating physician immediately.')),
    ('HbA1c', 'Endocrinology', '4.0-5.6', '%', 150.0,
     (None, 14.0, 'Severe hyperglycemia: confirm on analyzer and escalate.')),
    ('Lipid Profile', 'Cardiology', 'Varies', 'mg/dL', 200.0),
    ('Liver Function Test', 'Hepatic', 'Varies', 'U/L', 180.0),
    ('Kidney Function Test', 'Renal', 'Varies', 'mg/dL', 180.0),
    ('Thyroid Profile', 'Endocrinology', '0.4-4.0', 'mIU/L', 250.0,
     (None, 50.0, 'TSH suggesting thyroid storm: escalate immediately.')),
    ('Coagulation Panel', 'Hematology', 'Varies', 'sec', 220.0),
    ('Microbiology Culture', 'Microbiology', 'Negative', '', 350.0),
    ('Pathology Biopsy', 'Pathology', 'Negative', '', 500.0),
]

MEDICATIONS = [
    ('Acetaminophen', 'Panadol', 'Analgesic'),
    ('Amoxicillin', 'Amoxil', 'Antibiotic'),
    ('Metformin', 'Glucophage', 'Antidiabetic'),
    ('Atorvastatin', 'Lipitor', 'Statin'),
    ('Lisinopril', 'Zestril', 'ACE Inhibitor'),
    ('Amlodipine', 'Norvasc', 'Calcium Blocker'),
    ('Ibuprofen', 'Advil', 'NSAID'),
    ('Omeprazole', 'Prilosec', 'PPI'),
    ('Aspirin', 'Bayer', 'Antiplatelet'),
    ('Salbutamol', 'Ventolin', 'Bronchodilator'),
]


def seed_roles_and_permissions(app):
    """Create roles, permissions, and department/specialty catalogue.

    This is SAFE for production — it only inserts reference data that the
    application requires to function.  It is idempotent: running it twice
    does not create duplicates.
    """
    with app.app_context():
        print('Seeding roles and permissions...')
        for name, desc in ROLES.items():
            if not Role.query.filter_by(name=name).first():
                db.session.add(Role(name=name, description=desc))
        base_perms = ['create', 'read', 'update', 'delete', 'manage']
        for resource in ['patient', 'appointment', 'lab', 'radiology', 'prescription',
                         'pharmacy', 'staff', 'system']:
            for action in base_perms:
                pname = f'{action}_{resource}'
                if not Permission.query.filter_by(name=pname).first():
                    db.session.add(Permission(name=pname, resource=resource, action=action))
        db.session.commit()

        super_admin_role = Role.query.filter_by(name='SuperAdmin').first()
        for perm in Permission.query.all():
            if perm not in super_admin_role.permissions:
                super_admin_role.permissions.append(perm)

        created, assigned = seed_permissions(db)
        if created or assigned:
            print(f'  + Permissions seeded: {created} new, {assigned} role links')

        # Departments
        for d in DEPARTMENTS:
            if not Department.query.filter_by(name=d).first():
                db.session.add(Department(name=d))

        # Wards & beds
        WARDS = [
            ('General Ward A', 'General', 150.0, 12),
            ('Private Ward B', 'Private', 500.0, 6),
            ('ICU', 'ICU', 1200.0, 6),
        ]
        for name, wtype, charge, nbeds in WARDS:
            if not Ward.query.filter_by(name=name).first():
                ward = Ward(name=name, ward_type=wtype, room_charge_per_day=charge)
                db.session.add(ward)
                db.session.flush()
                for i in range(1, nbeds + 1):
                    db.session.add(Bed(ward_id=ward.id, bed_no=f'B{i:02d}'))

        # Specialties
        for s in SPECIALTIES:
            if not Specialty.query.filter_by(name=s).first():
                db.session.add(Specialty(name=s, description=f'{s} specialty'))
        for s in DENTAL_SPECIALTIES:
            if not DentalSpecialty.query.filter_by(name=s).first():
                db.session.add(DentalSpecialty(name=s, description=f'{s} dental specialty'))

        # Imaging types
        for name, desc, price in IMAGING_TYPES:
            if not ImagingType.query.filter_by(name=name).first():
                db.session.add(ImagingType(name=name, description=desc, price=price))

        # Lab test catalog
        for rec in LAB_TESTS:
            name, cat, nrange, unit, price = (list(rec) + [None] * 5)[:5]
            crit = rec[5] if len(rec) > 5 else None
            existing = LabTestCatalog.query.filter_by(test_name=name).first()
            if not existing:
                db.session.add(LabTestCatalog(
                    test_name=name, category=cat, normal_range=nrange, unit=unit,
                    price=price,
                    critical_low=crit[0] if crit else None,
                    critical_high=crit[1] if crit else None,
                    critical_notes=crit[2] if crit else None))
            elif crit and existing.critical_low is None and existing.critical_high is None:
                # Backfill thresholds for pre-existing seeded rows without
                # overwriting values an administrator may have configured.
                existing.critical_low = crit[0]
                existing.critical_high = crit[1]
                existing.critical_notes = crit[2]

        # Medications
        for generic, brand, cat in MEDICATIONS:
            if not Medication.query.filter_by(generic_name=generic).first():
                db.session.add(Medication(generic_name=generic, brand_name=brand, category=cat))

        # Drug interactions
        MED_BY_NAME = {m.generic_name: m for m in Medication.query.all()}
        INTERACTIONS = [
            ('Aspirin', 'Ibuprofen', 'Major',
             'Increased risk of gastrointestinal bleeding; avoid combination, or add gastroprotection.'),
            ('Lisinopril', 'Ibuprofen', 'Major',
             'NSAIDs may reduce antihypertensive effect and increase the risk of renal impairment.'),
            ('Lisinopril', 'Aspirin', 'Moderate',
             'Aspirin may attenuate the antihypertensive effect of ACE inhibitors.'),
            ('Amlodipine', 'Ibuprofen', 'Moderate',
             'NSAIDs may reduce the antihypertensive effect of calcium-channel blockers.'),
            ('Amlodipine', 'Atorvastatin', 'Moderate',
             'Reported myopathy risk mainly with high-dose atorvastatin (80 mg).'),
            ('Metformin', 'Ibuprofen', 'Moderate',
             'Risk of lactic acidosis in renal impairment; monitor renal function.'),
            ('Ibuprofen', 'Acetaminophen', 'Moderate',
             'Short-term combination is common; monitor for additive liver and GI effects.'),
            ('Aspirin', 'Salbutamol', 'Minor',
             'Rare bronchospasm in aspirin-sensitive asthmatics.'),
        ]
        for a_name, b_name, sev, desc in INTERACTIONS:
            ma, mb = MED_BY_NAME.get(a_name), MED_BY_NAME.get(b_name)
            if not ma or not mb:
                continue
            exists = DrugInteraction.query.filter_by(
                medication_a_id=ma.id, medication_b_id=mb.id).first()
            if not exists:
                db.session.add(DrugInteraction(
                    medication_a_id=ma.id, medication_b_id=mb.id,
                    severity=sev, description=desc))

        # System settings
        defaults = {
            'site_name': 'iHIS', 'sms_enabled': 'false', 'email_notifications': 'true',
            'appointment_duration_minutes': '30', 'session_timeout_minutes': '60',
        }
        for k, v in defaults.items():
            if not SystemSetting.query.filter_by(key=k).first():
                db.session.add(SystemSetting(key=k, value=v, category='general'))

        # Service catalog
        SERVICES = [
            ('General Consultation', 'Consultation', 150.0),
            ('Specialist Consultation', 'Consultation', 300.0),
            ('Emergency Room Visit', 'Consultation', 400.0),
            ('Private Room (per day)', 'Room', 500.0),
            ('Semi-Private Room (per day)', 'Room', 300.0),
            ('Ward Bed (per day)', 'Room', 150.0),
            ('Minor Procedure', 'Procedure', 600.0),
            ('Surgery (basic)', 'Procedure', 3000.0),
            ('Nursing Care (per day)', 'Other', 200.0),
        ]
        for name, cat, price in SERVICES:
            if not ServiceCatalog.query.filter_by(name=name).first():
                db.session.add(ServiceCatalog(name=name, category=cat, price=price,
                                              is_active=True))

        db.session.commit()
        print('Roles, permissions, and catalogue seeded.')


def seed_demo_data(app):
    """Create demo users and clinical records.  FOR DEVELOPMENT ONLY.

    This must NEVER be called in production.  It creates accounts with the
    trivial password '123456' and synthetic clinical data.
    """
    # Demo data must NEVER be seeded into a server-backed (production)
    # database.  Refuse if the configured DATABASE_URL is not SQLite,
    # regardless of the FLASK_CONFIG env var (which may be set by .env).
    db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if db_uri and not db_uri.startswith('sqlite:'):
        print('ERROR: Demo seed is prohibited for a server (non-SQLite) '
              'database. Use --roles-only to seed roles and permissions.')
        sys.exit(1)

    with app.app_context():
        print('Seeding demo data (development only)...')

        def create_user(username, email, password, full_name, user_type, roles,
                        department=None, **profile):
            if User.query.filter_by(email=email).first():
                return None
            user = User(username=username, email=email, full_name=full_name,
                        user_type=user_type)
            user.set_password(password)
            for r in roles:
                role = Role.query.filter_by(name=r).first()
                if role and role not in user.roles:
                    user.roles.append(role)
            if department:
                dept = Department.query.filter_by(name=department).first()
                if dept:
                    user.department_id = dept.id
            if 'specialty' in profile:
                _user_specialty[username] = profile['specialty']
            if 'dental_specialty' in profile:
                _user_dental_specialty[username] = profile['dental_specialty']
            db.session.add(user)
            return user

        def find_user(username):
            return User.query.filter_by(username=username).first()

        _user_specialty = {}
        _user_dental_specialty = {}

        create_user('superadmin', 'superadmin@ihis.com', '123456', 'System Owner',
                    'admin', ['SuperAdmin', 'Admin'])
        create_user('admin', 'admin@ihis.com', '123456', 'Hospital Administrator',
                    'admin', ['Admin'], department='Administration')
        create_user('dr_ahmed', 'dr.ahmed@ihis.com', '123456', 'Dr. Ahmed Mohamed',
                    'doctor', ['Doctor'], department='Internal Medicine',
                    specialty=SPECIALTIES[0])
        create_user('dr_sara', 'dr.sara@ihis.com', '123456', 'Dr. Sara Hassan',
                    'doctor', ['Doctor'], specialty='Cardiology')
        create_user('dr_amira', 'dr.amira@ihis.com', '123456', 'Dr. Amira AbdelRahman',
                    'doctor', ['Doctor'], department='Dermatology',
                    specialty='Dermatology')
        create_user('lab_tech', 'lab@ihis.com', '123456', 'Lab Technician',
                    'lab_technician', ['LabTechnician'], department='Pathology')
        create_user('radio', 'radio@ihis.com', '123456', 'Radiology Specialist',
                    'radiologist', ['Radiologist'], department='Radiology')
        create_user('radtech', 'radtech@ihis.com', '123456', 'Radiology Technologist Tarek',
                    'radiology_technician', ['RadiologyTechnician'], department='Radiology')
        create_user('pharma', 'pharma@ihis.com', '123456', 'Pharmacist',
                    'pharmacist', ['Pharmacist'], department='Pharmacy')
        create_user('nurse', 'nurse@ihis.com', '123456', 'Nurse Nour',
                    'nurse', ['Nurse'], department='Nursing')
        create_user('reception', 'reception@ihis.com', '123456', 'Receptionist Rana',
                    'receptionist', ['Receptionist'], department='Reception')
        create_user('dentist', 'dentist@ihis.com', '123456', 'Dr. Dental Dina',
                    'dentist', ['Dentist'], department='Dentistry',
                    dental_specialty=DENTAL_SPECIALTIES[0])
        create_user('physio', 'physio@ihis.com', '123456', 'Physical Therapist Peter',
                    'physiotherapist', ['Physiotherapist'], department='Rehabilitation')
        create_user('cashier', 'cashier@ihis.com', '123456', 'Cashier Camelia',
                    'cashier', ['Cashier'], department='Reception')
        create_user('patient_demo', 'patient@ihis.com', '123456', 'Demo Patient',
                    'patient', ['Patient'])
        db.session.commit()

        # Doctor profiles
        for u in User.query.filter_by(user_type='doctor').all():
            if not Doctor.query.filter_by(user_id=u.id).first():
                spec_name = (_user_specialty.get(u.username)
                             or u.username.replace('dr_', '').replace('dr.', ''))
                spec = (Specialty.query.filter_by(name=spec_name).first()
                        or Specialty.query.first())
                db.session.add(Doctor(user_id=u.id, specialty_id=spec.id if spec else None,
                                      license_number=f'LIC-{u.id}', years_of_experience=5,
                                      consultation_fee=150.0))

        # Dentist profile
        dentist_user = User.query.filter_by(username='dentist').first()
        if dentist_user and not Dentist.query.filter_by(user_id=dentist_user.id).first():
            dspec_name = _user_dental_specialty.get('dentist')
            dspec = (DentalSpecialty.query.filter_by(name=dspec_name).first()
                     if dspec_name else None) or DentalSpecialty.query.first()
            db.session.add(Dentist(user_id=dentist_user.id, dental_specialty_id=dspec.id if dspec else None,
                                   license_number='DEN-1', years_of_experience=6))

        # Physiotherapist profile
        physio_user = User.query.filter_by(username='physio').first()
        if physio_user and not PhysicalTherapist.query.filter_by(user_id=physio_user.id).first():
            db.session.add(PhysicalTherapist(user_id=physio_user.id, specialization='Orthopedic Rehabilitation',
                                             license_number='PT-1', years_of_experience=7))

        # Patient profile
        pat_user = User.query.filter_by(username='patient_demo').first()
        if pat_user and not Patient.query.filter_by(user_id=pat_user.id).first():
            demo_p = Patient(user_id=pat_user.id, phone='0111111111',
                             address='Cairo, Egypt', blood_type='O+', gender='Male',
                             allergies='Penicillin', chronic_diseases='Hypertension')
            db.session.add(demo_p)
            db.session.flush()
            assign_mrn(demo_p)
        # Self-heal: any patient row without an MRN gets one.
        for _p in Patient.query.filter(Patient.mrn.is_(None)).all():
            assign_mrn(_p)

        # Pharmacy inventory
        for med in Medication.query.all():
            if not PharmacyInventory.query.filter_by(medication_id=med.id).first():
                db.session.add(PharmacyInventory(medication_id=med.id, quantity=50,
                                                 reorder_level=10, unit_cost=5.0,
                                                 selling_price=15.0))

        db.session.commit()

        # Demo clinical records
        demo_patient = Patient.query.filter_by(user_id=pat_user.id).first() if pat_user else None
        demo_doctor = Doctor.query.first()
        if demo_patient and demo_doctor and not MedicalRecord.query.first():
            visit = datetime.utcnow() - timedelta(days=2)
            mr = MedicalRecord(patient_id=demo_patient.id, doctor_id=demo_doctor.id,
                               diagnosis='Type 2 Diabetes Mellitus',
                               treatment_plan='Diet control, Metformin 500 mg twice daily, regular monitoring.',
                               clinical_notes='Patient presented with fatigue and polyuria.',
                               visit_date=visit)
            db.session.add(mr)
            db.session.flush()
            db.session.add(Diagnosis(patient_id=demo_patient.id, doctor_id=demo_doctor.id,
                                     icd10_code='E11.9',
                                     description='Type 2 diabetes mellitus without complications',
                                     is_primary=True, date_diagnosed=visit))
            demo_rx = Prescription(patient_id=demo_patient.id, doctor_id=demo_doctor.id,
                                   refills=2, status='Active', prescribed_date=visit)
            db.session.add(demo_rx)
            db.session.flush()
            db.session.add(PrescriptionItem(
                prescription_id=demo_rx.id,
                medication_id=Medication.query.filter_by(generic_name='Metformin').first().id,
                dosage='500 mg', frequency='Twice daily', duration='30 days',
                instructions='Take after meals.', quantity=1, status='Active',
            ))
            db.session.commit()

        # ------------------------------------------------------------------
        # Demo clinical order sets + clinical templates (harvest: OpenEMR /
        # OpenMRS reference features, native reimplementation). New order sets
        # start INACTIVE and must be explicitly activated before applying.
        # ------------------------------------------------------------------
        def _spec_id(name):
            spec = Specialty.query.filter_by(name=name).first()
            return spec.id if spec else None

        if not OrderSet.query.filter_by(name='Admission Workup').first():
            oset = OrderSet(name='Admission Workup',
                            description='Standard adult medical admission workup.',
                            category='Standard',
                            specialty_id=_spec_id('Internal Medicine'),
                            is_published=True, is_active=True,
                            created_by=demo_doctor.user_id)
            db.session.add(oset)
            db.session.flush()
            cbc = LabTestCatalog.query.filter_by(
                test_name='Complete Blood Count (CBC)').first()
            if cbc:
                db.session.add(OrderSetItem(order_set_id=oset.id, item_type='LAB',
                                            lab_test_id=cbc.id,
                                            notes='Full blood count on admission'))
            lft = LabTestCatalog.query.filter_by(
                test_name='Liver Function Test').first()
            if lft:
                db.session.add(OrderSetItem(order_set_id=oset.id, item_type='LAB',
                                            lab_test_id=lft.id,
                                            notes='Liver function panel'))
            metf = Medication.query.filter_by(generic_name='Metformin').first()
            if metf:
                db.session.add(OrderSetItem(
                    order_set_id=oset.id, item_type='MEDICATION',
                    medication_id=metf.id, dosage='500 mg', frequency='Twice daily',
                    duration='30 days', quantity=60,
                    instructions='Take after meals.', priority='Normal'))
            db.session.flush()

        if not ClinicianTemplate.query.filter_by(title='Follow-up Consultation').first():
            import json as _json
            db.session.add(ClinicianTemplate(
                title='Follow-up Consultation', template_type='CONSULT',
                description='Structured follow-up note for return visits.',
                specialty_id=_spec_id('Internal Medicine'),
                sections=_json.dumps([
                    {'key': 'cc', 'label': 'Chief Complaint',
                     'placeholder': 'Reason for this visit...'},
                    {'key': 'sn', 'label': 'Interval History',
                     'placeholder': 'Changes since last visit...'},
                    {'key': 'oe', 'label': 'Examination',
                     'placeholder': 'Focused findings...'},
                    {'key': 'pl', 'label': 'Plan',
                     'placeholder': 'Investigations, medication, follow-up...'},
                ]),
                is_active=True, created_by=demo_doctor.user_id))
        db.session.commit()

        print('Demo data seeded.')
        print('All demo accounts use password: 123456')


def main():
    roles_only = '--roles-only' in sys.argv or '--roles' in sys.argv
    no_demo = '--no-demo' in sys.argv

    app = create_app('development')

    if roles_only:
        seed_roles_and_permissions(app)
    elif no_demo:
        seed_roles_and_permissions(app)
    else:
        seed_roles_and_permissions(app)
        seed_demo_data(app)


if __name__ == '__main__':
    main()
