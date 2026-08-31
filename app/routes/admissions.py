"""Admissions & Bed management: admit, allocate beds, discharge, ward overview.

When a patient is discharged the accrued room charge (ward.room_charge_per_day
* days stayed) is pushed into the billing subsystem automatically.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app import db
from app.models import (Admission, Ward, Bed, Patient, Doctor, Bill, BillItem)
from app.routes.decorators import roles_required, permissions_required, log_activity
from app.utils import utcnow

admissions_bp = Blueprint('admissions', __name__)

STAFF = ('Receptionist', 'Admin', 'SuperAdmin', 'Nurse')


def _next_admission_no():
    last = Admission.query.order_by(Admission.id.desc()).first()
    return f'ADM-{1000 + (last.id + 1 if last else 1)}'


@admissions_bp.route('/dashboard')
@login_required
@roles_required(*STAFF)
@permissions_required('ADMISSION_VIEW')
def dashboard():
    beds_total = Bed.query.count()
    beds_occupied = Bed.query.filter_by(status='Occupied').count()
    wards = Ward.query.count()
    current = Admission.query.filter_by(status='Admitted').order_by(
        Admission.admitted_at.desc()).all()
    beds_available = Bed.query.filter_by(status='Available').count()
    return render_template('admissions/dashboard.html', title='Admissions Dashboard',
                           beds_total=beds_total, beds_occupied=beds_occupied,
                           beds_available=beds_available, wards=wards, current=current)


@admissions_bp.route('/admissions')
@login_required
@roles_required(*STAFF)
@permissions_required('ADMISSION_VIEW')
def admissions():
    status = request.args.get('status', '').strip()
    query = Admission.query
    if status:
        query = query.filter(Admission.status == status)
    items = query.order_by(Admission.admitted_at.desc()).all()
    patients = Patient.query.all()
    wards = Ward.query.all()
    doctors = Doctor.query.all()
    # ward_id -> list of (bed_id, label) for available beds only
    ward_beds = {
        w.id: [(b.id, f'{w.name} — Bed {b.bed_no}') for b in w.beds if b.status == 'Available']
        for w in wards
    }
    return render_template('admissions/admissions.html', title='Admissions',
                           items=items, status=status, patients=patients,
                           wards=wards, doctors=doctors, ward_beds=ward_beds)


@admissions_bp.route('/admit', methods=['POST'])
@login_required
@roles_required('Receptionist', 'Admin', 'SuperAdmin')
@permissions_required('ADMISSION_CREATE')
def admit():
    patient_id = request.form.get('patient_id')
    ward_id = request.form.get('ward_id')
    bed_id = request.form.get('bed_id')
    doctor_id = request.form.get('doctor_id')
    reason = request.form.get('reason')
    expected = request.form.get('expected_discharge')

    if not patient_id or not ward_id or not bed_id:
        flash('Select a patient, ward, and bed.', 'danger')
        return redirect(url_for('admissions.admissions'))

    bed = Bed.query.get_or_404(int(bed_id))
    if bed.status != 'Available':
        flash(f'Bed {bed.bed_no} is not available.', 'danger')
        return redirect(url_for('admissions.admissions'))

    from datetime import datetime
    expected_dt = None
    if expected:
        try:
            expected_dt = datetime.strptime(expected, '%Y-%m-%d')
        except ValueError:
            expected_dt = None

    patient = db.session.get(Patient, int(patient_id))
    if patient is None:
        flash('Invalid patient.', 'danger')
        return redirect(url_for('admissions.admissions'))

    # A patient cannot be admitted twice while still admitted.
    active = Admission.query.filter_by(patient_id=patient.id, status='Admitted').first()
    if active:
        flash('Patient is already admitted.', 'warning')
        return redirect(url_for('admissions.admissions'))

    admission = Admission(
        admission_no=_next_admission_no(),
        patient_id=patient.id, ward_id=int(ward_id), bed_id=bed.id,
        admitting_doctor_id=int(doctor_id) if doctor_id else None,
        admitted_by=current_user.id, reason=reason,
        expected_discharge=expected_dt, status='Admitted')
    bed.status = 'Occupied'
    db.session.add(admission)
    db.session.flush()
    log_activity('ADMIT_PATIENT', 'admission', admission.id,
                 f'patient={patient.id} bed={bed.id}')
    from app.services.notifications import notify_patient
    notify_patient(patient, 'Admission confirmed',
                   f'You have been admitted to {bed.ward.name if bed.ward else "ward"} (bed {bed.bed_no}). Admission: {admission.admission_no}.')
    db.session.commit()
    flash(f'Patient admitted to bed {bed.bed_no} ({admission.admission_no}).', 'success')
    return redirect(url_for('admissions.dashboard'))


@admissions_bp.route('/admissions/<int:id>/discharge', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'SuperAdmin')
@permissions_required('ADMISSION_DISCHARGE')
def discharge(id):
    admission = Admission.query.get_or_404(id)
    if admission.status != 'Admitted':
        flash('Admission is not active.', 'warning')
        return redirect(url_for('admissions.dashboard'))
    if request.method == 'GET':
        return render_template('admissions/discharge.html', title='Discharge Patient',
                               admission=admission)
    notes = request.form.get('discharge_notes')
    admission.status = 'Discharged'
    admission.discharge_notes = notes
    admission.discharge_diagnosis = request.form.get('discharge_diagnosis')
    admission.discharge_summary = request.form.get('discharge_summary')
    admission.follow_up_instructions = request.form.get('follow_up_instructions')
    admission.discharge_medications = request.form.get('discharge_medications')
    admission.discharged_by = current_user.id
    admission.discharged_at = utcnow()
    if admission.bed:
        admission.bed.status = 'Available'

    # Accrue the room charge as a bill item on a room bill.
    if admission.ward and admission.ward.room_charge_per_day:
        days = admission.days_stayed() + 1  # count the discharge day
        charge = admission.ward.room_charge_per_day * days
        bill = Bill(patient_id=admission.patient_id, source_type='Room',
                    source_id=admission.id)
        db.session.add(bill)
        db.session.flush()
        db.session.add(BillItem(bill_id=bill.id,
                                description=f'{admission.ward.name} — {days} day(s)',
                                quantity=1, unit_price=charge))
    log_activity('DISCHARGE_PATIENT', 'admission', admission.id,
                 f'notes={"yes" if notes else "no"}')
    from app.services.notifications import notify_patient
    notify_patient(admission.patient, 'Discharged',
                   f'You have been discharged from {admission.ward.name if admission.ward else "the hospital"} '
                   f'after {admission.days_stayed()} day(s). Thank you for choosing us.')
    db.session.commit()
    flash(f'Patient discharged. {admission.days_stayed()} day(s) stayed.', 'success')
    return redirect(url_for('admissions.discharge_summary', id=admission.id))


@admissions_bp.route('/admissions/<int:id>/discharge-summary')
@login_required
@roles_required('Admin', 'SuperAdmin', 'Doctor', 'Nurse', 'Receptionist')
@permissions_required('ADMISSION_VIEW')
def discharge_summary(id):
    """Printable structured discharge summary (medical record lifecycle)."""
    admission = Admission.query.get_or_404(id)
    if admission.status != 'Discharged':
        flash('This admission has not been discharged yet.', 'warning')
        return redirect(url_for('admissions.dashboard'))
    bills = Bill.query.filter_by(patient_id=admission.patient_id).all()
    return render_template('admissions/discharge_summary.html', title='Discharge Summary',
                           admission=admission, bills=bills)


@admissions_bp.route('/wards')
@login_required
@roles_required('Admin', 'SuperAdmin')
@permissions_required('ADMISSION_VIEW')
def wards():
    items = Ward.query.order_by(Ward.name).all()
    return render_template('admissions/wards.html', title='Wards & Beds', items=items)


@admissions_bp.route('/wards/add', methods=['POST'])
@login_required
@roles_required('Admin', 'SuperAdmin')
@permissions_required('BED_MANAGE')
def add_ward():
    name = request.form.get('name')
    if not name:
        flash('Ward name is required.', 'danger')
        return redirect(url_for('admissions.wards'))
    ward = Ward(name=name,
                ward_type=request.form.get('ward_type') or 'General',
                floor=request.form.get('floor'),
                room_charge_per_day=float(request.form.get('room_charge_per_day') or 0))
    db.session.add(ward)
    db.session.flush()
    # Create N beds for the ward
    try:
        n = int(request.form.get('num_beds') or 0)
    except ValueError:
        n = 0
    for i in range(1, n + 1):
        db.session.add(Bed(ward_id=ward.id, bed_no=f'B{i:02d}'))
    db.session.commit()
    flash(f'Ward "{name}" created with {n} bed(s).', 'success')
    return redirect(url_for('admissions.wards'))
