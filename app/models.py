"""iHIS - Intelligent Health Information System
Complete SQLAlchemy data models.
Centralized digital healthcare ecosystem across all portals.
"""
from datetime import datetime, date
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db, login_manager, bcrypt


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ============================ Auth / RBAC ============================
user_roles = db.Table(
    'user_roles',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
)

role_permissions = db.Table(
    'role_permissions',
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    db.Column('permission_id', db.Integer, db.ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True),
)


class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.String(255))
    permissions = db.relationship(
        'Permission', secondary=role_permissions,
        lazy='subquery', backref=db.backref('roles', lazy=True))

    def __repr__(self):
        return f'<Role {self.name}>'


class Permission(db.Model):
    __tablename__ = 'permissions'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    resource = db.Column(db.String(100))
    action = db.Column(db.String(50))  # create / read / update / delete / manage

    def __repr__(self):
        return f'<Permission {self.name}>'


class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    user_type = db.Column(db.String(50), nullable=False)  # patient/doctor/nurse/...
    phone = db.Column(db.String(30))
    avatar = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    failed_login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    roles = db.relationship('Role', secondary=user_roles, lazy='subquery',
                            backref=db.backref('users', lazy=True))

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

    def has_role(self, role_name):
        return any(role.name == role_name for role in self.roles)

    def has_any_role(self, *role_names):
        return any(r.name in role_names for r in self.roles)

    def has_permission(self, permission_name):
        for role in self.roles:
            for perm in role.permissions:
                if perm.name == permission_name:
                    return True
        return False

    def __repr__(self):
        return f'<User {self.username}>'


# ============================ Core Clinical ============================
class Department(db.Model):
    __tablename__ = 'departments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    description = db.Column(db.Text)
    head_doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    staff = db.relationship('User', backref='department', lazy=True)


class Specialty(db.Model):
    __tablename__ = 'specialties'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    doctors = db.relationship('Doctor', backref='specialty', lazy=True)


class Doctor(db.Model):
    __tablename__ = 'doctors'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                       unique=True, nullable=False)
    specialty_id = db.Column(db.Integer, db.ForeignKey('specialties.id'))
    license_number = db.Column(db.String(50), unique=True)
    years_of_experience = db.Column(db.Integer)
    consultation_fee = db.Column(db.Float, default=0.0)
    digital_signature = db.Column(db.Text)
    user = db.relationship('User', backref=db.backref('doctor_profile', uselist=False))


class Patient(db.Model):
    __tablename__ = 'patients'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                       unique=True, nullable=False)
    date_of_birth = db.Column(db.Date)
    gender = db.Column(db.String(20))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    blood_type = db.Column(db.String(5))
    allergies = db.Column(db.Text)
    chronic_diseases = db.Column(db.Text)
    emergency_contact = db.Column(db.String(100))
    vaccination_records = db.Column(db.Text)
    ai_health_insights = db.Column(db.Text, default='{}')

    user = db.relationship('User', backref=db.backref('patient_profile', uselist=False))

    def medical_history(self):
        return self.medical_records


class MedicalRecord(db.Model):
    __tablename__ = 'medical_records'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id', ondelete='SET NULL'))
    diagnosis = db.Column(db.Text)
    treatment_plan = db.Column(db.Text)
    clinical_notes = db.Column(db.Text)
    visit_date = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('medical_records', lazy=True))
    doctor = db.relationship('Doctor', backref=db.backref('medical_records', lazy=True))


class Diagnosis(db.Model):
    __tablename__ = 'diagnoses'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id', ondelete='SET NULL'))
    icd10_code = db.Column(db.String(20))
    description = db.Column(db.String(255), nullable=False)
    is_primary = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)
    date_diagnosed = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('diagnoses', lazy=True))
    doctor = db.relationship('Doctor', backref=db.backref('diagnoses', lazy=True))


class PatientDocument(db.Model):
    __tablename__ = 'patient_documents'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(200))
    file_url = db.Column(db.String(255), nullable=False)
    document_type = db.Column(db.String(100))  # report/imaging/consent/insurance/other
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('documents', lazy=True))


class Medication(db.Model):
    __tablename__ = 'medications'
    id = db.Column(db.Integer, primary_key=True)
    generic_name = db.Column(db.String(150), nullable=False)
    brand_name = db.Column(db.String(150))
    category = db.Column(db.String(100))
    contraindications = db.Column(db.Text)
    side_effects = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)


class Prescription(db.Model):
    __tablename__ = 'prescriptions'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id', ondelete='SET NULL'))
    medication_id = db.Column(db.Integer, db.ForeignKey('medications.id'))
    dosage = db.Column(db.String(100))
    frequency = db.Column(db.String(100))
    duration = db.Column(db.String(100))
    instructions = db.Column(db.Text)
    refills = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), default='Active')  # Active / Dispensed / Completed / Cancelled
    prescribed_date = db.Column(db.DateTime, default=datetime.utcnow)
    digital_signature = db.Column(db.Text)

    patient = db.relationship('Patient', backref=db.backref('prescriptions', lazy=True))
    doctor = db.relationship('Doctor', backref=db.backref('prescriptions', lazy=True))
    medication = db.relationship('Medication', backref=db.backref('prescriptions', lazy=True))


class Appointment(db.Model):
    __tablename__ = 'appointments'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id', ondelete='CASCADE'), nullable=False)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    duration_minutes = db.Column(db.Integer, default=30)
    status = db.Column(db.String(50), default='Scheduled')  # Scheduled/Confirmed/CheckedIn/Completed/Cancelled/NoShow
    reason = db.Column(db.Text)
    priority = db.Column(db.String(20), default='Normal')
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('appointments', lazy=True))
    doctor = db.relationship('Doctor', backref=db.backref('appointments', lazy=True))


# ============================ Laboratory ============================
class LabTestCatalog(db.Model):
    __tablename__ = 'lab_test_catalog'
    id = db.Column(db.Integer, primary_key=True)
    test_name = db.Column(db.String(150), unique=True, nullable=False)
    category = db.Column(db.String(100))
    normal_range = db.Column(db.String(100))
    unit = db.Column(db.String(50))
    price = db.Column(db.Float, default=0.0)
    is_active = db.Column(db.Boolean, default=True)


class LabOrder(db.Model):
    __tablename__ = 'lab_orders'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id', ondelete='SET NULL'))
    test_id = db.Column(db.Integer, db.ForeignKey('lab_test_catalog.id'), nullable=False)
    status = db.Column(db.String(50), default='Pending')  # Pending/SampleCollected/InProgress/Completed
    priority = db.Column(db.String(20), default='Normal')
    order_date = db.Column(db.DateTime, default=datetime.utcnow)
    sample_collected_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)

    patient = db.relationship('Patient', backref=db.backref('lab_orders', lazy=True))
    doctor = db.relationship('Doctor', backref=db.backref('lab_orders', lazy=True))
    test = db.relationship('LabTestCatalog', backref=db.backref('orders', lazy=True))
    result = db.relationship('LabResult', backref='order', uselist=False, lazy=True,
                             cascade="all, delete-orphan")


class LabResult(db.Model):
    __tablename__ = 'lab_results'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('lab_orders.id', ondelete='CASCADE'),
                         unique=True, nullable=False)
    result_value = db.Column(db.String(100))
    result_notes = db.Column(db.Text)
    is_abnormal = db.Column(db.Boolean, default=False)
    validated_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    result_date = db.Column(db.DateTime, default=datetime.utcnow)
    pdf_report_url = db.Column(db.String(255))

    validator = db.relationship('User', backref=db.backref('validated_lab_results', lazy=True))


# ============================ Radiology ============================
class ImagingType(db.Model):
    __tablename__ = 'imaging_types'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, default=0.0)


class RadiologyOrder(db.Model):
    __tablename__ = 'radiology_orders'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id', ondelete='SET NULL'))
    imaging_type_id = db.Column(db.Integer, db.ForeignKey('imaging_types.id'), nullable=False)
    status = db.Column(db.String(50), default='Pending')  # Pending/Scheduled/InProgress/Completed
    priority = db.Column(db.String(20), default='Normal')
    order_date = db.Column(db.DateTime, default=datetime.utcnow)
    scanned_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    image_urls = db.Column(db.Text)  # Comma-separated or JSON

    patient = db.relationship('Patient', backref=db.backref('radiology_orders', lazy=True))
    doctor = db.relationship('Doctor', backref=db.backref('radiology_orders', lazy=True))
    imaging_type = db.relationship('ImagingType', backref=db.backref('orders', lazy=True))
    report = db.relationship('RadiologyReport', backref='order', uselist=False, lazy=True,
                             cascade="all, delete-orphan")


class RadiologyReport(db.Model):
    __tablename__ = 'radiology_reports'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('radiology_orders.id', ondelete='CASCADE'),
                         unique=True, nullable=False)
    findings = db.Column(db.Text)
    impression = db.Column(db.Text)
    recommendation = db.Column(db.Text)
    reported_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    report_date = db.Column(db.DateTime, default=datetime.utcnow)
    reporter = db.relationship('User', backref=db.backref('radiology_reports', lazy=True))


# ============================ Pharmacy ============================
class PharmacyInventory(db.Model):
    __tablename__ = 'pharmacy_inventory'
    id = db.Column(db.Integer, primary_key=True)
    medication_id = db.Column(db.Integer, db.ForeignKey('medications.id'), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    reorder_level = db.Column(db.Integer, default=10)
    unit_cost = db.Column(db.Float, default=0.0)
    selling_price = db.Column(db.Float, default=0.0)
    expiry_date = db.Column(db.Date)
    batch_number = db.Column(db.String(50))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    medication = db.relationship('Medication', backref=db.backref('inventory_items', lazy=True))


class DispensingRecord(db.Model):
    __tablename__ = 'dispensing_records'
    id = db.Column(db.Integer, primary_key=True)
    prescription_id = db.Column(db.Integer, db.ForeignKey('prescriptions.id', ondelete='CASCADE'),
                                nullable=False)
    pharmacist_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    quantity = db.Column(db.Integer, default=0)
    dispensed_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)
    prescription = db.relationship('Prescription', backref=db.backref('dispensing_records', lazy=True))


class DrugInteraction(db.Model):
    __tablename__ = 'drug_interactions'
    id = db.Column(db.Integer, primary_key=True)
    medication_a_id = db.Column(db.Integer, db.ForeignKey('medications.id'), nullable=False)
    medication_b_id = db.Column(db.Integer, db.ForeignKey('medications.id'), nullable=False)
    severity = db.Column(db.String(20), default='Moderate')
    description = db.Column(db.Text)


# ============================ Nursing ============================
class VitalSign(db.Model):
    __tablename__ = 'vital_signs'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    nurse_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    temperature = db.Column(db.Float)
    blood_pressure_systolic = db.Column(db.Integer)
    blood_pressure_diastolic = db.Column(db.Integer)
    heart_rate = db.Column(db.Integer)
    respiratory_rate = db.Column(db.Integer)
    oxygen_saturation = db.Column(db.Integer)
    height_cm = db.Column(db.Float)
    weight_kg = db.Column(db.Float)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('vital_signs', lazy=True))


class NursingNote(db.Model):
    __tablename__ = 'nursing_notes'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    nurse_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    note = db.Column(db.Text, nullable=False)
    shift = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    patient = db.relationship('Patient', backref=db.backref('nursing_notes', lazy=True))


class MedicationAdministration(db.Model):
    __tablename__ = 'medication_administrations'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    prescription_id = db.Column(db.Integer, db.ForeignKey('prescriptions.id'))
    nurse_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    administered_at = db.Column(db.DateTime, default=datetime.utcnow)
    dose_given = db.Column(db.String(100))
    status = db.Column(db.String(50), default='Given')  # Given/Skipped/Refused
    notes = db.Column(db.Text)


class CarePlan(db.Model):
    __tablename__ = 'care_plans'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    nurse_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    title = db.Column(db.String(200))
    goals = db.Column(db.Text)
    interventions = db.Column(db.Text)
    start_date = db.Column(db.Date, default=date.today)
    end_date = db.Column(db.Date)
    status = db.Column(db.String(50), default='Active')
    patient = db.relationship('Patient', backref=db.backref('care_plans', lazy=True))


# ============================ Dentistry ============================
class DentalSpecialty(db.Model):
    __tablename__ = 'dental_specialties'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)


class Dentist(db.Model):
    __tablename__ = 'dentists'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                       unique=True, nullable=False)
    dental_specialty_id = db.Column(db.Integer, db.ForeignKey('dental_specialties.id'))
    license_number = db.Column(db.String(50), unique=True)
    years_of_experience = db.Column(db.Integer)
    user = db.relationship('User', backref=db.backref('dentist_profile', uselist=False))


class DentalRecord(db.Model):
    __tablename__ = 'dental_records'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    dental_history = db.Column(db.Text)
    dental_allergies = db.Column(db.Text)
    previous_procedures = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    patient = db.relationship('Patient', backref=db.backref('dental_record', uselist=False))


class DentalChart(db.Model):
    __tablename__ = 'dental_charts'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    tooth_number = db.Column(db.String(10), nullable=False)  # FDI / Universal / Palmer
    numbering_system = db.Column(db.String(20), default='FDI')
    status = db.Column(db.String(50), default='Healthy')
    # Caries, Filling, Crown, Bridge, Implant, RootCanal, Extraction, Ortho, Missing
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    patient = db.relationship('Patient', backref=db.backref('dental_charts', lazy=True))


class DentalProcedure(db.Model):
    __tablename__ = 'dental_procedures'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'))
    procedure_name = db.Column(db.String(150), nullable=False)
    tooth_number = db.Column(db.String(10))
    cost = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text)
    performed_at = db.Column(db.DateTime, default=datetime.utcnow)
    patient = db.relationship('Patient', backref=db.backref('dental_procedures', lazy=True))


class DentalImage(db.Model):
    __tablename__ = 'dental_images'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    image_type = db.Column(db.String(50))  # Periapical/Bitewing/Panoramic/CBCT/Intraoral/Extraoral/3D
    url = db.Column(db.String(255))
    notes = db.Column(db.Text)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    patient = db.relationship('Patient', backref=db.backref('dental_images', lazy=True))


class OrthodonticCase(db.Model):
    __tablename__ = 'orthodontic_cases'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'))
    case_type = db.Column(db.String(100))
    appliance = db.Column(db.String(100))
    start_date = db.Column(db.Date, default=date.today)
    estimated_end_date = db.Column(db.Date)
    status = db.Column(db.String(50), default='Active')
    progress = db.Column(db.Integer, default=0)  # 0-100%
    notes = db.Column(db.Text)
    patient = db.relationship('Patient', backref=db.backref('orthodontic_cases', lazy=True))


# ============================ Physiotherapy & Rehab ============================
class PhysicalTherapist(db.Model):
    __tablename__ = 'physical_therapists'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                       unique=True, nullable=False)
    specialization = db.Column(db.String(100))
    license_number = db.Column(db.String(50), unique=True)
    years_of_experience = db.Column(db.Integer)
    user = db.relationship('User', backref=db.backref('therapist_profile', uselist=False))


class TherapyAssessment(db.Model):
    __tablename__ = 'therapy_assessments'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    therapist_id = db.Column(db.Integer, db.ForeignKey('physical_therapists.id'))
    functional_assessment = db.Column(db.Text)
    mobility_assessment = db.Column(db.Text)
    pain_assessment = db.Column(db.Integer)  # 0-10
    muscle_strength = db.Column(db.Text)
    balance_assessment = db.Column(db.Text)
    range_of_motion = db.Column(db.Text)
    posture_evaluation = db.Column(db.Text)
    gait_analysis = db.Column(db.Text)
    notes = db.Column(db.Text)
    assessed_at = db.Column(db.DateTime, default=datetime.utcnow)
    patient = db.relationship('Patient', backref=db.backref('therapy_assessments', lazy=True))


class TherapyPlan(db.Model):
    __tablename__ = 'therapy_plans'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    therapist_id = db.Column(db.Integer, db.ForeignKey('physical_therapists.id'))
    title = db.Column(db.String(200))
    goals = db.Column(db.Text)
    objectives = db.Column(db.Text)
    interventions = db.Column(db.Text)
    start_date = db.Column(db.Date, default=date.today)
    end_date = db.Column(db.Date)
    status = db.Column(db.String(50), default='Active')
    patient = db.relationship('Patient', backref=db.backref('therapy_plans', lazy=True))


class ExerciseLibraryItem(db.Model):
    __tablename__ = 'exercise_library'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    instructions = db.Column(db.Text)
    image_url = db.Column(db.String(255))
    video_url = db.Column(db.String(255))
    repetitions = db.Column(db.Integer)
    duration_seconds = db.Column(db.Integer)
    progression_plan = db.Column(db.Text)
    category = db.Column(db.String(100))


class TherapyExercise(db.Model):
    __tablename__ = 'therapy_exercises'
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('therapy_plans.id', ondelete='CASCADE'), nullable=False)
    library_item_id = db.Column(db.Integer, db.ForeignKey('exercise_library.id'))
    sets = db.Column(db.Integer)
    reps = db.Column(db.Integer)
    frequency = db.Column(db.String(100))
    notes = db.Column(db.Text)
    plan = db.relationship('TherapyPlan', backref=db.backref('exercises', lazy=True))


class TherapySession(db.Model):
    __tablename__ = 'therapy_sessions'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    therapist_id = db.Column(db.Integer, db.ForeignKey('physical_therapists.id'))
    plan_id = db.Column(db.Integer, db.ForeignKey('therapy_plans.id'))
    session_type = db.Column(db.String(100))  # Individual/Group/Home
    scheduled_at = db.Column(db.DateTime)
    duration_minutes = db.Column(db.Integer, default=45)
    status = db.Column(db.String(50), default='Scheduled')
    notes = db.Column(db.Text)
    patient = db.relationship('Patient', backref=db.backref('therapy_sessions', lazy=True))


class RehabilitationProgress(db.Model):
    __tablename__ = 'rehabilitation_progress'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('therapy_plans.id'))
    session_id = db.Column(db.Integer, db.ForeignKey('therapy_sessions.id'))
    pain_score = db.Column(db.Integer)
    mobility_score = db.Column(db.Integer)
    strength_score = db.Column(db.Integer)
    functional_outcome = db.Column(db.Integer)
    range_of_motion = db.Column(db.String(100))
    balance_score = db.Column(db.Integer)
    compliance = db.Column(db.Integer)  # 0-100%
    notes = db.Column(db.Text)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)


class FunctionalOutcome(db.Model):
    __tablename__ = 'functional_outcomes'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    assessment_type = db.Column(db.String(100))  # FIM/Barthel/TUG/...
    initial_score = db.Column(db.Float)
    current_score = db.Column(db.Float)
    target_score = db.Column(db.Float)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)


# ============================ Messaging & Notifications ============================
class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    subject = db.Column(db.String(200))
    body = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    sender = db.relationship('User', foreign_keys=[sender_id], backref='sent_messages')
    receiver = db.relationship('User', foreign_keys=[receiver_id], backref='received_messages')


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(200))
    message = db.Column(db.Text)
    notification_type = db.Column(db.String(50))  # in-app/email/sms/critical
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref=db.backref('notifications', lazy=True))


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    action = db.Column(db.String(150), nullable=False)
    resource = db.Column(db.String(100))
    resource_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class LoginAttempt(db.Model):
    __tablename__ = 'login_attempts'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    successful = db.Column(db.Boolean, default=False)
    ip_address = db.Column(db.String(64))
    attempted_at = db.Column(db.DateTime, default=datetime.utcnow)


class Referral(db.Model):
    __tablename__ = 'referrals'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    from_doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'))
    to_doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'))
    to_specialty = db.Column(db.String(100))
    reason = db.Column(db.Text)
    status = db.Column(db.String(50), default='Pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('referrals', lazy=True))
    from_doctor = db.relationship('Doctor', foreign_keys=[from_doctor_id])
    to_doctor = db.relationship('Doctor', foreign_keys=[to_doctor_id])


class CareTeam(db.Model):
    __tablename__ = 'care_teams'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(150))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('care_teams', lazy=True))


class CareTeamMember(db.Model):
    __tablename__ = 'care_team_members'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('care_teams.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    role = db.Column(db.String(100))
    team = db.relationship('CareTeam', backref=db.backref('members', lazy=True))
    user = db.relationship('User')


class MultidisciplinaryCase(db.Model):
    __tablename__ = 'multidisciplinary_cases'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(200))
    description = db.Column(db.Text)
    status = db.Column(db.String(50), default='Open')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('md_cases', lazy=True))


# ============================ AI & System ============================
class AIRecommendation(db.Model):
    __tablename__ = 'ai_recommendations'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'))
    recommendation_type = db.Column(db.String(50))
    content = db.Column(db.Text)
    confidence_score = db.Column(db.Float)
    is_applied = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    patient = db.relationship('Patient', backref=db.backref('ai_recommendations', lazy=True))


class SystemSetting(db.Model):
    __tablename__ = 'system_settings'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    category = db.Column(db.String(100))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
