"""iHIS Reports blueprint: generate and download clinical/admin PDF reports."""
from io import BytesIO

from flask import Blueprint, abort, render_template, send_file, session
from flask_login import login_required, current_user

from app.models import (
    MedicalRecord, LabOrder, RadiologyOrder, Prescription,
    PharmacyInventory, Patient, User, Diagnosis,
)
from app.routes.decorators import roles_required, log_activity
from app.services.reports import (
    medical_record_pdf, lab_result_pdf, radiology_report_pdf,
    prescription_pdf, inventory_pdf, statistics_pdf,
)

reports_bp = Blueprint('reports', __name__)


def _send(pdf_bytes, filename):
    log_activity('GENERATE_REPORT', 'report', None, filename)
    return send_file(
        BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename,
    )


@reports_bp.route('/')
@login_required
def dashboard():
    """Central HTML dashboard linking to all downloadable PDF reports."""
    is_staff = current_user.has_any_role(
        'Admin', 'SuperAdmin', 'Doctor', 'Nurse', 'Physiotherapist',
        'LabTechnician', 'Radiologist', 'Pharmacist', 'Dentist', 'Receptionist',
    )
    state = {
        'can_statistics': current_user.has_any_role('Admin', 'SuperAdmin'),
        'can_inventory': current_user.has_any_role('Pharmacist', 'Admin', 'SuperAdmin'),
        'is_patient': current_user.user_type == 'patient',
        'clinical': current_user.has_any_role(
            'Doctor', 'Nurse', 'Physiotherapist', 'Dentist',
            'LabTechnician', 'Radiologist',
        ),
    }
    return render_template('reports/dashboard.html', title='Reports', state=state, is_staff=is_staff)


def _patient_report_access(patient):
    """A user may view a patient's report if patient themselves, their doctor,
    or a staff member (admin/super admin)."""
    if current_user.user_type == 'patient':
        return patient and current_user.id == patient.user_id
    if current_user.has_any_role('Admin', 'SuperAdmin'):
        return True
    if current_user.has_any_role('Doctor', 'Nurse', 'Physiotherapist',
                                 'LabTechnician', 'Radiologist', 'Pharmacist',
                                 'Dentist', 'Receptionist'):
        return True
    return False


@reports_bp.route('/medical-record/<int:record_id>')
@login_required
def medical_record(record_id):
    record = MedicalRecord.query.get_or_404(record_id)
    if not _patient_report_access(record.patient):
        abort(403)
    diagnoses = Diagnosis.query.filter_by(patient_id=record.patient_id).all()
    prescriptions = Prescription.query.filter_by(patient_id=record.patient_id).all()
    patient_name = record.patient.user.full_name if record.patient else "-"
    doctor_name = (record.doctor.user.full_name if record.doctor and record.doctor.user else None)
    pdf = medical_record_pdf(patient_name, doctor_name, record, diagnoses, prescriptions)
    return _send(pdf, f"medical_record_{record_id}.pdf")


@reports_bp.route('/lab-result/<int:order_id>')
@login_required
def lab_result(order_id):
    order = LabOrder.query.get_or_404(order_id)
    if not _patient_report_access(order.patient):
        abort(403)
    if not order.result:
        abort(404)
    pdf = lab_result_pdf(order, order.result)
    return _send(pdf, f"lab_result_{order_id}.pdf")


@reports_bp.route('/radiology-report/<int:order_id>')
@login_required
def radiology_report(order_id):
    order = RadiologyOrder.query.get_or_404(order_id)
    if not _patient_report_access(order.patient):
        abort(403)
    if not order.report:
        abort(404)
    pdf = radiology_report_pdf(order, order.report)
    return _send(pdf, f"radiology_report_{order_id}.pdf")


@reports_bp.route('/prescription/<int:prescription_id>')
@login_required
def prescription(prescription_id):
    rx = Prescription.query.get_or_404(prescription_id)
    if not _patient_report_access(rx.patient):
        abort(403)
    pdf = prescription_pdf(rx)
    return _send(pdf, f"prescription_{prescription_id}.pdf")


@reports_bp.route('/inventory')
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
def inventory():
    items = PharmacyInventory.query.order_by(PharmacyInventory.quantity).all()
    pdf = inventory_pdf(items)
    return _send(pdf, "pharmacy_inventory.pdf")


@reports_bp.route('/statistics')
@login_required
@roles_required('Admin', 'SuperAdmin')
def statistics():
    users_count = User.query.count()
    patients = Patient.query.count()
    records = MedicalRecord.query.count()
    lab_orders = LabOrder.query.count()
    radiology = RadiologyOrder.query.count()
    prescriptions = Prescription.query.count()
    inventory = PharmacyInventory.query.count()
    rows = [
        ("Total Users", users_count),
        ("Total Patients", patients),
        ("Medical Records", records),
        ("Lab Orders", lab_orders),
        ("Radiology Orders", radiology),
        ("Prescriptions", prescriptions),
        ("Pharmacy Inventory Items", inventory),
    ]
    pdf = statistics_pdf("Hospital Operational Statistics", rows)
    return _send(pdf, "hospital_statistics.pdf")
