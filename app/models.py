"""iHIS - Intelligent Health Information System
Complete SQLAlchemy data models.
Centralized digital healthcare ecosystem across all portals.
"""
from datetime import date
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db, login_manager, bcrypt
from app.utils import utcnow


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
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

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
    head_doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id', use_alter=True,
                                                         name='fk_departments_head_doctor_id'))
    created_at = db.Column(db.DateTime, default=utcnow)
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
    mrn = db.Column(db.String(30), unique=True, index=True)  # Medical Record Number
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
    visit_date = db.Column(db.DateTime, default=utcnow)
    created_at = db.Column(db.DateTime, default=utcnow)
    status = db.Column(db.String(20), default='Draft')  # Draft -> Signed -> Locked
    signed_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    signed_at = db.Column(db.DateTime)
    amended_from_id = db.Column(db.Integer, db.ForeignKey('medical_records.id', ondelete='SET NULL'))

    patient = db.relationship('Patient', backref=db.backref('medical_records', lazy=True))
    doctor = db.relationship('Doctor', backref=db.backref('medical_records', lazy=True))
    signer = db.relationship('User', foreign_keys=[signed_by],
                             backref=db.backref('signed_medical_records', lazy=True))
    amended_from = db.relationship('MedicalRecord', remote_side=[id],
                                   backref=db.backref('amendments', lazy=True))


class Diagnosis(db.Model):
    __tablename__ = 'diagnoses'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id', ondelete='SET NULL'))
    icd10_code = db.Column(db.String(20))
    description = db.Column(db.String(255), nullable=False)
    is_primary = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)
    date_diagnosed = db.Column(db.DateTime, default=utcnow)

    patient = db.relationship('Patient', backref=db.backref('diagnoses', lazy=True))
    doctor = db.relationship('Doctor', backref=db.backref('diagnoses', lazy=True))


class PatientDocument(db.Model):
    __tablename__ = 'patient_documents'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(200))
    file_url = db.Column(db.String(255), nullable=False)
    document_type = db.Column(db.String(100))  # report/imaging/consent/insurance/other
    uploaded_at = db.Column(db.DateTime, default=utcnow)

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
    status = db.Column(db.String(50), default='Active')  # Active / Dispensed / Completed / Cancelled
    refills = db.Column(db.Integer, default=0)
    prescribed_date = db.Column(db.DateTime, default=utcnow)
    digital_signature = db.Column(db.Text)

    patient = db.relationship('Patient', backref=db.backref('prescriptions', lazy=True))
    doctor = db.relationship('Doctor', backref=db.backref('prescriptions', lazy=True))
    items = db.relationship('PrescriptionItem', backref='prescription', lazy=True,
                            cascade='all, delete-orphan',
                            order_by='PrescriptionItem.id')

    def dispensed(self):
        return all(i.status == 'Dispensed' for i in self.items) if self.items else False

    def fully_dispensed(self):
        """All items either fully dispensed or cancelled (nothing pending)."""
        if not self.items:
            return False
        return all(i.status in ('Dispensed', 'Cancelled') for i in self.items)

    def total_items(self):
        return len(self.items)


class PrescriptionItem(db.Model):
    __tablename__ = 'prescription_items'
    id = db.Column(db.Integer, primary_key=True)
    prescription_id = db.Column(db.Integer, db.ForeignKey('prescriptions.id', ondelete='CASCADE'),
                                nullable=False)
    medication_id = db.Column(db.Integer, db.ForeignKey('medications.id'), nullable=False)
    dosage = db.Column(db.String(100))
    frequency = db.Column(db.String(100))
    duration = db.Column(db.String(100))
    instructions = db.Column(db.Text)
    quantity = db.Column(db.Integer, default=1)
    status = db.Column(db.String(50), default='Active')  # Active / Dispensed / Cancelled

    medication = db.relationship('Medication', backref=db.backref('prescription_items', lazy=True))

    def dispensed_qty(self):
        """Cumulative quantity already dispensed for this item (from records)."""
        return sum(r.quantity or 0 for r in self.dispensing_records)

    def remaining_qty(self):
        """Quantity still owed to the patient (ordered minus dispensed)."""
        return max(0, (self.quantity or 0) - self.dispensed_qty())

    @property
    def display_status(self):
        if self.status == 'Cancelled':
            return 'Cancelled'
        if self.status == 'Dispensed':
            return 'Dispensed'
        if self.dispensed_qty() > 0:
            return 'Partially Dispensed'
        return self.status or 'Active'


class Appointment(db.Model):
    __tablename__ = 'appointments'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id', ondelete='CASCADE'), nullable=False)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    duration_minutes = db.Column(db.Integer, default=30)
    status = db.Column(db.String(50), default='Scheduled')
    # Scheduled/Confirmed/CheckedIn/InConsultation/Completed/Cancelled/NoShow
    reason = db.Column(db.Text)
    priority = db.Column(db.String(20), default='Normal')
    visit_type = db.Column(db.String(20), default='Scheduled')  # Scheduled/WalkIn
    queue_number = db.Column(db.Integer)          # assigned arrival queue position for the day
    checked_in_at = db.Column(db.DateTime)        # actual arrival/check-in time
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=utcnow)

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
    status = db.Column(db.String(50), default='Pending')
    # ORDERED -> ACCEPTED -> COLLECTED -> PROCESSING -> RESULTED -> VERIFIED -> FINALIZED
    priority = db.Column(db.String(20), default='Normal')
    order_date = db.Column(db.DateTime, default=utcnow)
    specimen_type = db.Column(db.String(100))          # Blood/Urine/Sputum/Stool/Serum/CSF...
    accession_number = db.Column(db.String(50), index=True)  # lab sample accession/barcode
    barcode = db.Column(db.String(50))
    collected_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    specimen_status = db.Column(db.String(50), default='NotCollected')
    # NotCollected/Collected/Received/Processing/Rejected/ReceivedAtLab
    collection_time = db.Column(db.DateTime)
    received_at_lab = db.Column(db.DateTime)
    rejection_reason = db.Column(db.String(250))
    reordered_from = db.Column(db.Integer)             # order id this re-test came from
    sample_collected_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)

    patient = db.relationship('Patient', backref=db.backref('lab_orders', lazy=True))
    doctor = db.relationship('Doctor', backref=db.backref('lab_orders', lazy=True))
    test = db.relationship('LabTestCatalog', backref=db.backref('orders', lazy=True))
    collector = db.relationship('User', foreign_keys=[collected_by])
    result = db.relationship('LabResult', backref='order', uselist=False, lazy=True,
                             cascade="all, delete-orphan")


class LabResult(db.Model):
    __tablename__ = 'lab_results'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('lab_orders.id', ondelete='CASCADE'),
                         unique=True, nullable=False)
    result_value = db.Column(db.String(100))
    result_notes = db.Column(db.Text)
    result_unit = db.Column(db.String(50))       # unit actually used at entry
    is_abnormal = db.Column(db.Boolean, default=False)
    is_critical = db.Column(db.Boolean, default=False)  # critical panic value
    qualitative = db.Column(db.String(50))       # Positive/Negative/Reactive/Non-reactive/Detected/Not detected
    validated_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    result_date = db.Column(db.DateTime, default=utcnow)
    pdf_report_url = db.Column(db.String(255))
    status = db.Column(db.String(50), default='Draft')  # Draft -> Verified -> Locked
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    amended_from_id = db.Column(db.Integer, db.ForeignKey('lab_results.id', ondelete='SET NULL'))

    validator = db.relationship('User', foreign_keys=[validated_by],
                                backref=db.backref('validated_lab_results', lazy=True))
    creator = db.relationship('User', foreign_keys=[created_by],
                              backref=db.backref('created_lab_results', lazy=True))
    amended_from = db.relationship('LabResult', remote_side=[id],
                                   backref=db.backref('amendments', lazy=True))


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
    status = db.Column(db.String(50), default='Pending')
    # ORDERED -> SCHEDULED -> ARRIVED -> IN_PROGRESS -> PERFORMED -> REPORTED -> SIGNED -> FINALIZED
    priority = db.Column(db.String(20), default='Normal')
    order_date = db.Column(db.DateTime, default=utcnow)
    scheduled_at = db.Column(db.DateTime)         # study scheduling time
    arrived_at = db.Column(db.DateTime)           # patient arrived for study
    performed_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    performed_at = db.Column(db.DateTime)
    technical_notes = db.Column(db.Text)
    scanned_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    image_urls = db.Column(db.Text)  # Comma-separated or JSON

    patient = db.relationship('Patient', backref=db.backref('radiology_orders', lazy=True))
    doctor = db.relationship('Doctor', backref=db.backref('radiology_orders', lazy=True))
    imaging_type = db.relationship('ImagingType', backref=db.backref('orders', lazy=True))
    technologist = db.relationship('User', foreign_keys=[performed_by])
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
    report_date = db.Column(db.DateTime, default=utcnow)
    status = db.Column(db.String(50), default='Draft')  # Draft -> Signed -> Locked
    signed_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    amended_from_id = db.Column(db.Integer, db.ForeignKey('radiology_reports.id', ondelete='SET NULL'))
    reporter = db.relationship('User', foreign_keys=[reported_by],
                               backref=db.backref('radiology_reports', lazy=True))
    signer = db.relationship('User', foreign_keys=[signed_by],
                             backref=db.backref('signed_radiology_reports', lazy=True))
    amended_from = db.relationship('RadiologyReport', remote_side=[id],
                                   backref=db.backref('amendments', lazy=True))


# ============================ Billing ============================
class ServiceCatalog(db.Model):
    """Billable services (consultations, procedures, room/day, etc.).

    Clinical orders carry their own prices (LabTestCatalog.price,
    ImagingType.price, Doctor.consultation_fee, PharmacyInventory.selling_price),
    but ad-hoc / non-encoded services are billed through this catalog.
    """
    __tablename__ = 'service_catalog'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100), default='General')  # Consultation/Room/Procedure/Other
    price = db.Column(db.Float, default=0.0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def __repr__(self):
        return f'<ServiceCatalog {self.name}>'


class Bill(db.Model):
    __tablename__ = 'bills'
    id = db.Column(db.Integer, primary_key=True)
    bill_no = db.Column(db.String(50), unique=True, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'),
                          nullable=False, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    status = db.Column(db.String(20), default='Unpaid')  # Unpaid / PartiallyPaid / Paid / Voided
    discount = db.Column(db.Float, default=0.0)
    tax_percent = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text)
    issued_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    source_type = db.Column(db.String(50))  # Manual/Consultation/Lab/Radiology/Pharmacy/Room
    source_id = db.Column(db.Integer)  # id of the triggering clinical record

    patient = db.relationship('Patient', backref=db.backref('bills', lazy=True))
    creator = db.relationship('User', foreign_keys=[created_by],
                              backref=db.backref('created_bills', lazy=True))
    items = db.relationship('BillItem', backref='bill', lazy=True,
                            cascade='all, delete-orphan',
                            order_by='BillItem.id')
    payments = db.relationship('Payment', backref='bill', lazy=True,
                               cascade='all, delete-orphan',
                               order_by='Payment.received_at')

    def subtotal(self):
        return sum(i.total() for i in self.items)

    def tax_amount(self):
        return self.subtotal() * (self.tax_percent / 100.0)

    def total(self):
        return self.subtotal() + self.tax_amount() - self.discount

    def paid_amount(self):
        return sum(p.amount for p in self.payments)

    def balance(self):
        return max(0.0, self.total() - self.paid_amount())

    def __repr__(self):
        return f'<Bill {self.bill_no}>'


class BillItem(db.Model):
    __tablename__ = 'bill_items'
    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey('bills.id', ondelete='CASCADE'), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    unit_price = db.Column(db.Float, default=0.0)
    service_catalog_id = db.Column(db.Integer, db.ForeignKey('service_catalog.id', ondelete='SET NULL'))

    def total(self):
        return self.quantity * self.unit_price

    def __repr__(self):
        return f'<BillItem {self.description} x{self.quantity}>'


class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey('bills.id', ondelete='CASCADE'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(30), default='Cash')  # Cash/Card/Insurance/BankTransfer/Other
    reference = db.Column(db.String(100))  # card ref / insurance claim no / receipt no
    received_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    receipt_no = db.Column(db.String(50))
    received_at = db.Column(db.DateTime, default=utcnow)
    notes = db.Column(db.Text)

    receiver = db.relationship('User', foreign_keys=[received_by])

    def __repr__(self):
        return f'<Payment {self.amount} on bill {self.bill_id}>'


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
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    medication = db.relationship('Medication', backref=db.backref('inventory_items', lazy=True))


class DispensingRecord(db.Model):
    __tablename__ = 'dispensing_records'
    id = db.Column(db.Integer, primary_key=True)
    prescription_id = db.Column(db.Integer, db.ForeignKey('prescriptions.id', ondelete='CASCADE'),
                                nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('prescription_items.id', ondelete='SET NULL'))
    pharmacist_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    quantity = db.Column(db.Integer, default=0)
    dispensed_at = db.Column(db.DateTime, default=utcnow)
    notes = db.Column(db.Text)
    prescription = db.relationship('Prescription', backref=db.backref('dispensing_records', lazy=True))
    item = db.relationship('PrescriptionItem', backref=db.backref('dispensing_records', lazy=True))
    pharmacist = db.relationship('User', foreign_keys=[pharmacist_id])


class StockTransaction(db.Model):
    """Audited ledger of every pharmacy stock movement.

    Every addition or withdrawal of inventory must create a transaction row,
    so the full history of each batch/medication is reconstructable. No
    movement happens without a transaction.
    """
    __tablename__ = 'stock_transactions'
    id = db.Column(db.Integer, primary_key=True)
    inventory_id = db.Column(db.Integer, db.ForeignKey('pharmacy_inventory.id', ondelete='SET NULL'))
    medication_id = db.Column(db.Integer, db.ForeignKey('medications.id'), nullable=False)
    tx_type = db.Column(db.String(30), nullable=False)  # PURCHASE/RECEIVE/DISPENSE/RETURN/ADJUSTMENT/TRANSFER/EXPIRED/DAMAGED
    quantity_change = db.Column(db.Integer, nullable=False)  # signed delta
    quantity_after = db.Column(db.Integer, default=0)
    unit_cost = db.Column(db.Float, default=0.0)
    reference = db.Column(db.String(100))  # e.g. prescription # / batch reference
    notes = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    inventory = db.relationship('PharmacyInventory', backref=db.backref('transactions', lazy=True))
    medication = db.relationship('Medication')
    user = db.relationship('User')


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
    pain_score = db.Column(db.Integer)          # 0-10 numeric pain rating
    blood_glucose = db.Column(db.Float)         # mg/dL
    recorded_at = db.Column(db.DateTime, default=utcnow)

    patient = db.relationship('Patient', backref=db.backref('vital_signs', lazy=True))


class IntakeOutput(db.Model):
    """Fluid intake/output balance record (nursing)."""
    __tablename__ = 'intake_output'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    nurse_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    intake_type = db.Column(db.String(50))      # Oral/IV/NG/Blood products/Other
    intake_ml = db.Column(db.Integer, default=0)
    output_type = db.Column(db.String(50))      # Urine/Stool/Drain/Vomit/Other
    output_ml = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    recorded_at = db.Column(db.DateTime, default=utcnow)
    patient = db.relationship('Patient', backref=db.backref('intake_output', lazy=True))


class NursingNote(db.Model):
    __tablename__ = 'nursing_notes'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    nurse_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    note = db.Column(db.Text, nullable=False)
    shift = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=utcnow)
    patient = db.relationship('Patient', backref=db.backref('nursing_notes', lazy=True))


class MedicationAdministration(db.Model):
    """Medication administration record (MAR).

    A physician's prescription item is turned into scheduled doses (one record
    per planned administration) with a due time. A nurse transitions each dose
    through the administered/refused/held/missed states. The original
    physician prescription is never edited by nursing.
    """
    __tablename__ = 'medication_administrations'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    prescription_id = db.Column(db.Integer, db.ForeignKey('prescriptions.id'))
    prescription_item_id = db.Column(db.Integer, db.ForeignKey('prescription_items.id'))
    medication_id = db.Column(db.Integer, db.ForeignKey('medications.id'))
    nurse_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    administered_at = db.Column(db.DateTime)          # when actually given
    scheduled_time = db.Column(db.DateTime, index=True)  # planned due time
    dose_given = db.Column(db.String(100))
    route = db.Column(db.String(50))                  # Oral/IV/IM/SC/Topical/Inhalation
    status = db.Column(db.String(50), default='Scheduled', index=True)
    # Scheduled / Due / Administered / Refused / Held / Missed
    reason = db.Column(db.String(200))                # for Refused/Held/Missed
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)

    patient = db.relationship('Patient', backref=db.backref('medication_administrations', lazy=True))
    prescription = db.relationship('Prescription', backref=db.backref('administrations', lazy=True))
    prescription_item = db.relationship('PrescriptionItem', backref=db.backref('administrations', lazy=True))
    medication = db.relationship('Medication')


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


# ============================ Admissions & Beds ============================
class Ward(db.Model):
    __tablename__ = 'wards'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'))
    floor = db.Column(db.String(50))
    ward_type = db.Column(db.String(50), default='General')  # General/Private/SemiPrivate/ICU
    room_charge_per_day = db.Column(db.Float, default=0.0)
    beds = db.relationship('Bed', backref='ward', lazy=True,
                           cascade='all, delete-orphan', order_by='Bed.bed_no')


class Bed(db.Model):
    __tablename__ = 'beds'
    id = db.Column(db.Integer, primary_key=True)
    ward_id = db.Column(db.Integer, db.ForeignKey('wards.id', ondelete='CASCADE'),
                        nullable=False)
    bed_no = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='Available')  # Available/Occupied/Reserved/Maintenance


class Admission(db.Model):
    __tablename__ = 'admissions'
    id = db.Column(db.Integer, primary_key=True)
    admission_no = db.Column(db.String(50), unique=True, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    ward_id = db.Column(db.Integer, db.ForeignKey('wards.id'))
    bed_id = db.Column(db.Integer, db.ForeignKey('beds.id', ondelete='SET NULL'))
    admitting_doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id', ondelete='SET NULL'))
    admitted_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    admitted_at = db.Column(db.DateTime, default=utcnow)
    expected_discharge = db.Column(db.DateTime)
    reason = db.Column(db.Text)  # provisional diagnosis / reason for admission
    status = db.Column(db.String(20), default='Admitted')  # Admitted/Discharged/Moved
    discharge_notes = db.Column(db.Text)
    discharge_diagnosis = db.Column(db.Text)
    discharge_summary = db.Column(db.Text)
    follow_up_instructions = db.Column(db.Text)
    discharge_medications = db.Column(db.Text)
    discharged_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    discharged_at = db.Column(db.DateTime)

    patient = db.relationship('Patient', backref=db.backref('admissions', lazy=True))
    ward = db.relationship('Ward', backref=db.backref('admissions', lazy=True))
    bed = db.relationship('Bed', backref=db.backref('admissions', lazy=True))
    admitting_doctor = db.relationship('Doctor', foreign_keys=[admitting_doctor_id])
    admitter = db.relationship('User', foreign_keys=[admitted_by])
    discharger = db.relationship('User', foreign_keys=[discharged_by])

    def days_stayed(self):
        end = self.discharged_at or utcnow()
        return max(0, (end - self.admitted_at).days)


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
    complaint = db.Column(db.Text)
    examination_findings = db.Column(db.Text)
    diagnosis = db.Column(db.Text)
    periodontal_notes = db.Column(db.Text)
    treatment_plan = db.Column(db.Text)
    dental_history = db.Column(db.Text)
    dental_allergies = db.Column(db.Text)
    previous_procedures = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    patient = db.relationship('Patient', backref=db.backref('dental_record', uselist=False))


class DentalChart(db.Model):
    __tablename__ = 'dental_charts'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    tooth_number = db.Column(db.String(10), nullable=False)  # FDI / Universal / Palmer
    numbering_system = db.Column(db.String(20), default='FDI')
    status = db.Column(db.String(50), default='Healthy')
    # Caries, Filling, Crown, Bridge, Implant, RootCanal, Extraction, Ortho, Missing
    surface = db.Column(db.String(20))        # M/D/B/L/O/MOD etc
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    patient = db.relationship('Patient', backref=db.backref('dental_charts', lazy=True))


class DentalTreatmentPlan(db.Model):
    """A named dental treatment plan that groups multiple procedures."""
    __tablename__ = 'dental_treatment_plans'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'))
    title = db.Column(db.String(200), nullable=False)
    diagnosis = db.Column(db.Text)
    status = db.Column(db.String(50), default='Planned')
    # Planned/Scheduled/InProgress/Completed/Cancelled
    start_date = db.Column(db.Date, default=date.today)
    end_date = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    procedures = db.relationship('DentalProcedure', backref='treatment_plan', lazy=True)
    patient = db.relationship('Patient', backref=db.backref('dental_treatment_plans', lazy=True))


class DentalProcedure(db.Model):
    __tablename__ = 'dental_procedures'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'))
    treatment_plan_id = db.Column(db.Integer, db.ForeignKey('dental_treatment_plans.id',
                                                            ondelete='SET NULL'))
    procedure_name = db.Column(db.String(150), nullable=False)
    tooth_number = db.Column(db.String(10))
    status = db.Column(db.String(50), default='Planned')
    # Planned/Scheduled/InProgress/Completed/Cancelled
    scheduled_at = db.Column(db.DateTime)
    cost = db.Column(db.Float, default=0.0)
    materials = db.Column(db.String(250))
    notes = db.Column(db.Text)
    performed_at = db.Column(db.DateTime, default=utcnow)
    completed_at = db.Column(db.DateTime)
    patient = db.relationship('Patient', backref=db.backref('dental_procedures', lazy=True))


class DentalImage(db.Model):
    __tablename__ = 'dental_images'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    image_type = db.Column(db.String(50))  # Periapical/Bitewing/Panoramic/CBCT/Intraoral/Extraoral/3D
    url = db.Column(db.String(255))
    notes = db.Column(db.Text)
    uploaded_at = db.Column(db.DateTime, default=utcnow)
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
    assessment_type = db.Column(db.String(30), default='Initial')  # Initial/FollowUp/Reassessment
    functional_assessment = db.Column(db.Text)
    mobility_assessment = db.Column(db.Text)
    pain_assessment = db.Column(db.Integer)  # 0-10
    muscle_strength = db.Column(db.Text)
    balance_assessment = db.Column(db.Text)
    range_of_motion = db.Column(db.Text)
    posture_evaluation = db.Column(db.Text)
    gait_analysis = db.Column(db.Text)
    notes = db.Column(db.Text)
    assessed_at = db.Column(db.DateTime, default=utcnow)
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
    precautions = db.Column(db.Text)          # contraindications / precautions
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
    started_at = db.Column(db.DateTime)        # actual start (check-in)
    settled_at = db.Column(db.DateTime)        # completed time
    duration_minutes = db.Column(db.Integer, default=45)
    status = db.Column(db.String(50), default='Scheduled')
    # Scheduled -> CheckedIn -> InProgress -> Completed / Cancelled / NoShow / Followup
    pain_before = db.Column(db.Integer)        # 0-10
    pain_after = db.Column(db.Integer)         # 0-10
    exercises_performed = db.Column(db.Text)   # summary of exercises done
    modalities = db.Column(db.String(250))     # e.g. heat, ultrasound, TENS
    patient_response = db.Column(db.Text)
    adherence = db.Column(db.Integer)          # 0-100%
    followup_required = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    patient = db.relationship('Patient', backref=db.backref('therapy_sessions', lazy=True))
    plan = db.relationship('TherapyPlan', backref=db.backref('sessions', lazy=True))


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
    recorded_at = db.Column(db.DateTime, default=utcnow)


class FunctionalOutcome(db.Model):
    __tablename__ = 'functional_outcomes'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    assessment_type = db.Column(db.String(100))  # FIM/Barthel/TUG/...
    initial_score = db.Column(db.Float)
    current_score = db.Column(db.Float)
    target_score = db.Column(db.Float)
    recorded_at = db.Column(db.DateTime, default=utcnow)


# ============================ Messaging & Notifications ============================
class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    subject = db.Column(db.String(200))
    body = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    sent_at = db.Column(db.DateTime, default=utcnow)
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
    entity_type = db.Column(db.String(50))     # e.g. lab_order, radiology_order, task, prescription, referral
    entity_id = db.Column(db.Integer)          # resource/pk the notification links to
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    user = db.relationship('User', backref=db.backref('notifications', lazy=True))


class Task(db.Model):
    """Global reusable work/task item for the hospital.

    A single task engine is shared across departments; each department has
    its own task types but works from the same queue semantics (NEW/ASSIGNED/
    IN_PROGRESS/COMPLETED/...). Tasks may be generated automatically from
    orders (lab, radiology, pharmacy, referral, etc.) or created manually.
    """
    __tablename__ = 'tasks'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    task_type = db.Column(db.String(50))       # LAB/radiology/pharmacy/nursing/referral/document/review/...
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'))
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    assigned_to = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    assigned_role = db.Column(db.String(50))
    department = db.Column(db.String(100))
    priority = db.Column(db.String(20), default='Normal')
    # LOW/NORMAL/HIGH/URGENT/CRITICAL
    status = db.Column(db.String(20), default='NEW', index=True)
    # NEW/ASSIGNED/IN_PROGRESS/ON_HOLD/COMPLETED/CANCELLED/REJECTED
    due_at = db.Column(db.DateTime)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    related_resource_type = db.Column(db.String(50))  # lab_order, radiology_order, prescription, referral, admission...
    related_resource_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    patient = db.relationship('Patient', backref=db.backref('tasks', lazy=True))
    creator = db.relationship('User', foreign_keys=[created_by], backref=db.backref('created_tasks', lazy=True))
    assignee = db.relationship('User', foreign_keys=[assigned_to], backref=db.backref('assigned_tasks', lazy=True))
    activities = db.relationship('TaskActivity', backref='task', lazy=True,
                                 cascade='all, delete-orphan', order_by='TaskActivity.created_at')


class TaskActivity(db.Model):
    """Audit trail of a task's lifecycle (assigned/started/completed/rejected/...)."""
    __tablename__ = 'task_activities'
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    action = db.Column(db.String(50))         # CREATED/ASSIGNED/STARTED/COMPLETED/REJECTED/...
    from_status = db.Column(db.String(20))
    to_status = db.Column(db.String(20))
    note = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=utcnow)
    user = db.relationship('User')


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), index=True)
    action = db.Column(db.String(150), nullable=False, index=True)
    resource = db.Column(db.String(100))
    resource_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    reason = db.Column(db.Text)
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=utcnow, index=True)


class LoginAttempt(db.Model):
    __tablename__ = 'login_attempts'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    successful = db.Column(db.Boolean, default=False)
    ip_address = db.Column(db.String(64))
    attempted_at = db.Column(db.DateTime, default=utcnow)


class Referral(db.Model):
    __tablename__ = 'referrals'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    from_doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'))
    to_doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'))
    to_specialty = db.Column(db.String(100))
    reason = db.Column(db.Text)
    status = db.Column(db.String(50), default='Pending')
    created_at = db.Column(db.DateTime, default=utcnow)

    patient = db.relationship('Patient', backref=db.backref('referrals', lazy=True))
    from_doctor = db.relationship('Doctor', foreign_keys=[from_doctor_id])
    to_doctor = db.relationship('Doctor', foreign_keys=[to_doctor_id])


class CareTeam(db.Model):
    __tablename__ = 'care_teams'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(150))
    created_at = db.Column(db.DateTime, default=utcnow)

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
    created_at = db.Column(db.DateTime, default=utcnow)

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
    created_at = db.Column(db.DateTime, default=utcnow)
    patient = db.relationship('Patient', backref=db.backref('ai_recommendations', lazy=True))


class SystemSetting(db.Model):
    __tablename__ = 'system_settings'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    category = db.Column(db.String(100))
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
