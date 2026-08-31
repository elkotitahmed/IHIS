"""Seed data for iHIS - creates roles, departments, specialties, imaging types,
test catalog, medications, and demo users for every portal.
"""
from datetime import datetime, timedelta
from app import create_app, db
from app.permissions import seed_permissions
from app.models import (
    User, Role, Permission, Department, Specialty, Doctor, Patient,
    ImagingType, LabTestCatalog, Medication, PharmacyInventory,
    DentalSpecialty, Dentist, PhysicalTherapist, SystemSetting,
    MedicalRecord, Diagnosis, Prescription, PrescriptionItem, LabOrder, LabResult,
    RadiologyOrder, RadiologyReport, Referral, CareTeam, CareTeamMember,
    MultidisciplinaryCase, Appointment, VitalSign, NursingNote, CarePlan,
    DentalRecord, DentalChart, TherapyAssessment, TherapyPlan,
    Notification, PatientDocument, ServiceCatalog, Bill, BillItem, Payment,
    Ward, Bed, Admission,
)

app = create_app('development')

# Complete role set matching the prompt's portals
ROLES = {
    'SuperAdmin': 'Full system control',
    'Admin': 'Hospital administration',
    'Doctor': 'Clinical care and EMR',
    'Patient': 'Patient portal access',
    'Nurse': 'Nursing and vitals',
    'LabTechnician': 'Laboratory operations',
    'Radiologist': 'Radiology operations',
    'Pharmacist': 'Pharmacy operations',
    'Receptionist': 'Appointment and reception',
    'Dentist': 'Dental care',
    'Physiotherapist': 'Physical therapy and rehab',
}

DEPARTMENTS = ['Emergency', 'Internal Medicine', 'Cardiology', 'Surgery', 'Radiology',
               'Pathology', 'Nursing', 'Pharmacy', 'Reception', 'Dentistry',
               'Rehabilitation']

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
    ('Complete Blood Count (CBC)', 'Hematology', '4.0-11.0', 'x10^9/L', 100.0),
    ('HbA1c', 'Endocrinology', '4.0-5.6', '%', 150.0),
    ('Lipid Profile', 'Cardiology', 'Varies', 'mg/dL', 200.0),
    ('Liver Function Test', 'Hepatic', 'Varies', 'U/L', 180.0),
    ('Kidney Function Test', 'Renal', 'Varies', 'mg/dL', 180.0),
    ('Thyroid Profile', 'Endocrinology', '0.4-4.0', 'mIU/L', 250.0),
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


def main():
    with app.app_context():
        print('Starting seed...')

        # Roles
        for name, desc in ROLES.items():
            if not Role.query.filter_by(name=name).first():
                db.session.add(Role(name=name, description=desc))
        # Permissions
        base_perms = ['create', 'read', 'update', 'delete', 'manage']
        for resource in ['patient', 'appointment', 'lab', 'radiology', 'prescription',
                         'pharmacy', 'staff', 'system']:
            for action in base_perms:
                pname = f'{action}_{resource}'
                if not Permission.query.filter_by(name=pname).first():
                    db.session.add(Permission(name=pname, resource=resource, action=action))
        db.session.commit()

        # SuperAdmin role gets all permissions
        super_admin_role = Role.query.filter_by(name='SuperAdmin').first()
        for perm in Permission.query.all():
            if perm not in super_admin_role.permissions:
                super_admin_role.permissions.append(perm)

        # Seed the granular, record-level permission catalog onto default roles
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
        # Dental specialties
        for s in DENTAL_SPECIALTIES:
            if not DentalSpecialty.query.filter_by(name=s).first():
                db.session.add(DentalSpecialty(name=s, description=f'{s} dental specialty'))
        # Imaging types
        for name, desc, price in IMAGING_TYPES:
            if not ImagingType.query.filter_by(name=name).first():
                db.session.add(ImagingType(name=name, description=desc, price=price))
        # Lab test catalog
        for name, cat, nrange, unit, price in LAB_TESTS:
            if not LabTestCatalog.query.filter_by(test_name=name).first():
                db.session.add(LabTestCatalog(test_name=name, category=cat,
                                              normal_range=nrange, unit=unit, price=price))
        # Medications
        for generic, brand, cat in MEDICATIONS:
            if not Medication.query.filter_by(generic_name=generic).first():
                db.session.add(Medication(generic_name=generic, brand_name=brand, category=cat))
        db.session.commit()

        def create_user(username, email, password, full_name, user_type, roles,
                        department=None, **profile):
            if User.query.filter_by(email=email).first():
                print(f'  - {email} already exists')
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
            db.session.add(user)
            print(f'  + Created {full_name} ({email})')
            return user

        # Demo users per portal
        create_user('superadmin', 'superadmin@ihis.com', '123456', 'System Owner',
                    'admin', ['SuperAdmin', 'Admin'])
        create_user('admin', 'admin@ihis.com', '123456', 'Hospital Administrator',
                    'admin', ['Admin'], department='Administration')
        create_user('dr_ahmed', 'dr.ahmed@ihis.com', '123456', 'Dr. Ahmed Mohamed',
                    'doctor', ['Doctor'], department='Internal Medicine',
                    specialty=SPECIALTIES[0])
        create_user('dr_sara', 'dr.sara@ihis.com', '123456', 'Dr. Sara Hassan',
                    'doctor', ['Doctor'], specialty='Cardiology')
        create_user('lab_tech', 'lab@ihis.com', '123456', 'Lab Technician',
                    'lab_technician', ['LabTechnician'], department='Pathology')
        create_user('radio', 'radio@ihis.com', '123456', 'Radiology Specialist',
                    'radiologist', ['Radiologist'], department='Radiology')
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
        create_user('patient_demo', 'patient@ihis.com', '123456', 'Demo Patient',
                    'patient', ['Patient'])

        # Create doctor profiles for doctors
        for u in User.query.filter_by(user_type='doctor').all():
            if not Doctor.query.filter_by(user_id=u.id).first():
                spec = Specialty.query.filter_by(name=u.username.replace('dr_', '').replace('dr.', '')).first() or Specialty.query.first()
                db.session.add(Doctor(user_id=u.id, specialty_id=spec.id if spec else None,
                                      license_number=f'LIC-{u.id}', years_of_experience=5,
                                      consultation_fee=150.0))
        # Create dentist profile
        dentist_user = User.query.filter_by(username='dentist').first()
        if dentist_user and not Dentist.query.filter_by(user_id=dentist_user.id).first():
            dspec = DentalSpecialty.query.first()
            db.session.add(Dentist(user_id=dentist_user.id, dental_specialty_id=dspec.id if dspec else None,
                                   license_number='DEN-1', years_of_experience=6))
        # Create physiotherapist profile
        physio_user = User.query.filter_by(username='physio').first()
        if physio_user and not PhysicalTherapist.query.filter_by(user_id=physio_user.id).first():
            db.session.add(PhysicalTherapist(user_id=physio_user.id, specialization='Orthopedic Rehabilitation',
                                             license_number='PT-1', years_of_experience=7))
        # Create patient profile
        pat_user = User.query.filter_by(username='patient_demo').first()
        if pat_user and not Patient.query.filter_by(user_id=pat_user.id).first():
            db.session.add(Patient(user_id=pat_user.id, phone='0111111111',
                                   address='Cairo, Egypt', blood_type='O+', gender='Male',
                                   allergies='Penicillin', chronic_diseases='Hypertension'))

        # Pharmacy inventory
        for med in Medication.query.all():
            if not PharmacyInventory.query.filter_by(medication_id=med.id).first():
                db.session.add(PharmacyInventory(medication_id=med.id, quantity=50,
                                                 reorder_level=10, unit_cost=5.0,
                                                 selling_price=15.0))

        # System settings
        defaults = {
            'site_name': 'iHIS', 'sms_enabled': 'false', 'email_notifications': 'true',
            'appointment_duration_minutes': '30', 'session_timeout_minutes': '60',
        }
        for k, v in defaults.items():
            if not SystemSetting.query.filter_by(key=k).first():
                db.session.add(SystemSetting(key=k, value=v, category='general'))

        # Service catalog (billable ad-hoc services)
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

        # Demo clinical records (make reports & portals functional out of the box)
        demo_patient = Patient.query.filter_by(user_id=pat_user.id).first() if pat_user else None
        demo_doctor = Doctor.query.first()
        if demo_patient and demo_doctor and not MedicalRecord.query.first():
            visit = datetime.utcnow() - timedelta(days=2)

            mr = MedicalRecord(patient_id=demo_patient.id, doctor_id=demo_doctor.id,
                               diagnosis='Type 2 Diabetes Mellitus',
                               treatment_plan='Diet control, Metformin 500 mg twice daily, regular monitoring.',
                               clinical_notes='Patient presented with fatigue and polyuria. '
                                              'Random blood glucose elevated. Education on diet and lifestyle provided.',
                               visit_date=visit)
            db.session.add(mr)
            db.session.flush()
            db.session.add(Diagnosis(patient_id=demo_patient.id, doctor_id=demo_doctor.id,
                                     icd10_code='E11.9',
                                     description='Type 2 diabetes mellitus without complications',
                                     is_primary=True, date_diagnosed=visit))

            demo_prescription = Prescription(patient_id=demo_patient.id, doctor_id=demo_doctor.id,
                                             refills=2, status='Active', prescribed_date=visit)
            db.session.add(demo_prescription)
            db.session.flush()
            db.session.add(PrescriptionItem(
                prescription_id=demo_prescription.id,
                medication_id=Medication.query.filter_by(generic_name='Metformin').first().id,
                dosage='500 mg', frequency='Twice daily', duration='30 days',
                instructions='Take after meals. Monitor blood glucose regularly.',
                quantity=1, status='Active',
            ))

            hba1c = LabTestCatalog.query.filter_by(test_name='HbA1c').first()
            if hba1c:
                lo = LabOrder(patient_id=demo_patient.id, doctor_id=demo_doctor.id,
                              test_id=hba1c.id, status='Completed', priority='Normal',
                              order_date=visit)
                db.session.add(lo)
                db.session.flush()
                lab_user = User.query.filter_by(username='lab_tech').first()
                db.session.add(LabResult(order_id=lo.id, result_value='8.2',
                                         result_notes='Fasting sample. Above reference range.',
                                         is_abnormal=True,
                                         validated_by=lab_user.id if lab_user else None))

            xray = ImagingType.query.filter_by(name='X-Ray').first()
            if xray:
                ro = RadiologyOrder(patient_id=demo_patient.id, doctor_id=demo_doctor.id,
                                    imaging_type_id=xray.id, status='Completed',
                                    order_date=visit)
                db.session.add(ro)
                db.session.flush()
                radio_user = User.query.filter_by(username='radio').first()
                db.session.add(RadiologyReport(order_id=ro.id,
                                               findings='Chest X-ray shows clear lung fields. '
                                                        'No consolidations, effusions, or pneumothorax.',
                                               impression='Normal chest radiograph.',
                                               recommendation='No further imaging required at this time.',
                                               reported_by=radio_user.id if radio_user else None))

        # Demo care coordination (referrals, care teams, MD cases)
        if demo_patient and demo_doctor and not Referral.query.first():
            db.session.add(Referral(patient_id=demo_patient.id, from_doctor_id=demo_doctor.id,
                                    to_specialty='Cardiology',
                                    reason='Evaluate chronic hypertension and chest discomfort.',
                                    status='Pending'))
            team = CareTeam(patient_id=demo_patient.id, name='Diabetes Care Team')
            db.session.add(team)
            db.session.flush()
            nurse_user = User.query.filter_by(username='nurse').first()
            physio_user = User.query.filter_by(username='physio').first()
            for u, role in [(demo_doctor.user_id, 'Endocrinologist'),
                            (nurse_user.id if nurse_user else None, 'Care Coordinator'),
                            (physio_user.id if physio_user else None, 'Rehabilitation Specialist')]:
                if u:
                    db.session.add(CareTeamMember(team_id=team.id, user_id=u, role=role))
            db.session.add(MultidisciplinaryCase(patient_id=demo_patient.id,
                                                 title='Diabetes management review',
                                                 description='Multi-specialty review of glycemic control and lifestyle plan.',
                                                 status='Open'))

        # Demo appointments
        if demo_patient and demo_doctor and not Appointment.query.first():
            db.session.add(Appointment(patient_id=demo_patient.id, doctor_id=demo_doctor.id,
                                       scheduled_at=datetime.utcnow() + timedelta(days=1),
                                       duration_minutes=30, status='Scheduled',
                                       reason='Diabetes follow-up', priority='Normal',
                                       created_by=demo_doctor.user_id))

        # Demo nursing data
        if demo_patient and not VitalSign.query.first():
            nurse_user = User.query.filter_by(username='nurse').first()
            db.session.add(VitalSign(patient_id=demo_patient.id,
                                     nurse_id=nurse_user.id if nurse_user else None,
                                     temperature=37.2, blood_pressure_systolic=130,
                                     blood_pressure_diastolic=85, heart_rate=78,
                                     respiratory_rate=16, oxygen_saturation=97,
                                     height_cm=170, weight_kg=80))
            db.session.add(NursingNote(patient_id=demo_patient.id,
                                       nurse_id=nurse_user.id if nurse_user else None,
                                       note='Patient stable. Blood sugar monitored post-meal.',
                                       shift='Morning'))
            db.session.add(CarePlan(patient_id=demo_patient.id,
                                    nurse_id=nurse_user.id if nurse_user else None,
                                    title='Diabetes management plan',
                                    goals='Maintain HbA1c < 7%, stable vitals, diet adherence.',
                                    interventions='Daily glucose checks, medication reminders, dietary counseling.',
                                    status='Active'))

        # Demo dental record
        if demo_patient and not DentalRecord.query.first():
            dentist_user = User.query.filter_by(username='dentist').first()
            db.session.add(DentalRecord(patient_id=demo_patient.id,
                                        dental_history='Routine checkups; mild gingivitis.',
                                        dental_allergies='Latex sensitivity.',
                                        previous_procedures='Scaling and polishing 6 months ago.'))
            db.session.add(DentalChart(patient_id=demo_patient.id, tooth_number='16',
                                       numbering_system='FDI', status='Filling',
                                       notes='Composite filling on occlusal surface.'))

        # Demo physiotherapy data
        if demo_patient and not TherapyAssessment.query.first():
            physio_user = User.query.filter_by(username='physio').first()
            therapist = PhysicalTherapist.query.filter_by(user_id=physio_user.id).first() if physio_user else None
            db.session.add(TherapyAssessment(patient_id=demo_patient.id,
                                             therapist_id=therapist.id if therapist else None,
                                             functional_assessment='Independent in ADLs with mild limitation.',
                                             mobility_assessment='Reduced knee flexion right side.',
                                             pain_assessment=3, muscle_strength='4/5 lower extremities',
                                             balance_assessment='Good static balance.',
                                             range_of_motion='Knee flexion 110 deg right / 135 deg left',
                                             posture_evaluation='Mild forward head posture.',
                                             gait_analysis='Normal gait with slight antalgic pattern.',
                                             notes='Referred by primary physician for knee stiffness.'))
            db.session.add(TherapyPlan(patient_id=demo_patient.id,
                                       therapist_id=therapist.id if therapist else None,
                                       title='Knee rehabilitation plan',
                                       goals='Restore full ROM and strength.',
                                       objectives='Increase knee flexion to 120 deg within 4 weeks.',
                                       interventions='Stretching, quadriceps strengthening, balance training.',
                                       status='Active'))

        # Demo billing record
        if demo_patient and not Bill.query.first():
            reception_user = User.query.filter_by(username='reception').first()
            bill = Bill(patient_id=demo_patient.id,
                        created_by=reception_user.id if reception_user else None,
                        status='Paid', bill_no='INV-1001',
                        source_type='Manual', notes='General consultation + room charge')
            db.session.add(bill)
            db.session.flush()
            db.session.add(BillItem(bill_id=bill.id, description='General Consultation',
                                    quantity=1, unit_price=150.0))
            db.session.add(BillItem(bill_id=bill.id, description='Ward Bed (per day)',
                                    quantity=1, unit_price=150.0))
            db.session.flush()
            db.session.add(Payment(bill_id=bill.id, amount=300.0, method='Cash',
                                   reference='', received_by=reception_user.id if reception_user else None,
                                   receipt_no='RCT-10001'))

        # Demo admission
        if demo_patient and not Admission.query.first():
            ward = Ward.query.filter_by(name='General Ward A').first()
            bed = ward.beds[0] if ward and ward.beds else None
            if ward and bed and bed.status == 'Available':
                admission = Admission(admission_no='ADM-1001',
                                      patient_id=demo_patient.id, ward_id=ward.id,
                                      bed_id=bed.id,
                                      admitting_doctor_id=demo_doctor.id if demo_doctor else None,
                                      reason='Admitted for diabetes stabilization and monitoring.',
                                      status='Admitted')
                bed.status = 'Occupied'
                db.session.add(admission)

        # Demo notifications for every user account
        if User.query.count() and not Notification.query.first():
            special = {
                'patient': ('Appointment Reminder', 'Your follow-up with Dr. Ahmed is scheduled for next week.', 'in-app', False),
                'doctor': ('New Referral', 'A new patient referral has been assigned to you.', 'in-app', False),
                'nurse': ('Care Plan Assigned', 'A new care plan requires nursing attention.', 'in-app', False),
                'reception': ('Patient Check-in', 'A patient is scheduled for arrival this afternoon.', 'in-app', True),
                'admin': ('Daily Summary', 'System activity summary for today is ready.', 'email', True),
                'superadmin': ('System Health Check', 'All services are operating normally.', 'email', True),
                'lab': ('Lab Order', 'A new lab order is waiting for processing.', 'in-app', False),
                'radio': ('Imaging Order', 'A new radiology order is waiting for processing.', 'in-app', False),
                'pharma': ('Prescription Queue', 'New prescriptions are awaiting dispensation.', 'in-app', False),
                'dentist': ('Dental Appointment', 'A dental follow-up is scheduled.', 'in-app', False),
                'physio': ('Therapy Plan', 'A new physiotherapy plan has been created.', 'in-app', False),
            }
            for u in User.query.all():
                spec = special.get(u.user_type)
                if not spec:
                    spec = ('Welcome to iHIS', f'Welcome {u.full_name}!', 'in-app', False)
                db.session.add(Notification(user_id=u.id, title=spec[0], message=spec[1],
                                            notification_type=spec[2], is_read=spec[3]))

        # Demo patient document (with a sample file on disk, kept out of static/)
        if demo_patient and not PatientDocument.query.first():
            from pathlib import Path
            from flask import current_app
            base = current_app.config.get('UPLOAD_FOLDER') or 'var/uploads'
            samples = Path(base) / 'medical_documents'
            samples.mkdir(parents=True, exist_ok=True)
            sample_file = samples / 'sample_lab_result.txt'
            sample_file.write_text(
                'Sample Laboratory Report\nHbA1c: 6.4% | Fasting Glucose: 118 mg/dL\n',
                encoding='utf-8')
            db.session.add(PatientDocument(patient_id=demo_patient.id,
                                           title='Sample Lab Result',
                                           document_type='report',
                                           file_url='medical_documents/sample_lab_result.txt'))

        db.session.commit()
        print('Seed complete!')
        print('=' * 50)
        print('All demo accounts use password: 123456')
        print('  superadmin@ihis.com, admin@ihis.com, dr.ahmed@ihis.com,')
        print('  lab@ihis.com, radio@ihis.com, pharma@ihis.com, nurse@ihis.com,')
        print('  reception@ihis.com, dentist@ihis.com, physio@ihis.com, patient@ihis.com')
        print('=' * 50)


if __name__ == '__main__':
    main()
