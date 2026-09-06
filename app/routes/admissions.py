"""Admissions & Bed management: admit, allocate beds, discharge, ward overview.

When a patient is discharged the accrued room charge (ward.room_charge_per_day
* days stayed) is pushed into the billing subsystem automatically.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app import db
from app.models import (Admission, Ward, Bed, Patient, Doctor, Bill, BillItem,
                        ClinicalAlert)
from app.routes.decorators import roles_required, permissions_required, log_activity
from app.utils import utcnow
from app.services.timeline import record_event
from app.services.billing import bill_number_for
from app.access import require_patient_access

admissions_bp = Blueprint('admissions', __name__)

STAFF = ('Receptionist', 'Admin', 'SuperAdmin', 'Nurse', 'Doctor')


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
    beds_available = Bed.query.filter_by(status='Available').count()
    current = Admission.query.filter_by(status='Admitted').order_by(
        Admission.admitted_at.desc()).all()

    # ---- Ward census (per-ward occupancy board) ----
    ward_census = []
    for w in Ward.query.order_by(Ward.name.asc()).all():
        beds = list(w.beds)
        total = len(beds)
        occupied = sum(1 for b in beds if b.status == 'Occupied')
        available = sum(1 for b in beds if b.status == 'Available')
        ward_census.append({
            'ward': w,
            'total': total, 'occupied': occupied, 'available': available,
            'pct': round((occupied / total) * 100) if total else 0,
            'full': total > 0 and occupied >= total,
        })

    # ---- Needs attention: overstays + open high/critical alerts ----
    now = utcnow()
    overstay_pids = {a.patient_id for a in current
                     if a.expected_discharge and a.expected_discharge < now}
    open_alert_counts = {}
    if current:
        rows = (ClinicalAlert.query
                .filter(ClinicalAlert.patient_id.in_([a.patient_id for a in current]),
                        ClinicalAlert.status == 'OPEN',
                        ClinicalAlert.severity.in_(['HIGH', 'CRITICAL']))
                .all())
        for r in rows:
            open_alert_counts[r.patient_id] = open_alert_counts.get(r.patient_id, 0) + 1

    attention = []
    for a in current:
        flags = []
        days_over = 0
        if a.patient_id in overstay_pids:
            flags.append('overstay')
            days_over = max(0, (now - a.expected_discharge).days)
        if a.patient_id in open_alert_counts:
            flags.append('alert')
        if flags:
            attention.append({'admission': a, 'flags': flags, 'days_over': days_over,
                              'alerts': open_alert_counts.get(a.patient_id, 0)})

    return render_template('admissions/dashboard.html', title='Admissions Dashboard',
                           beds_total=beds_total, beds_occupied=beds_occupied,
                           beds_available=beds_available, wards=wards, current=current,
                           ward_census=ward_census, attention=attention,
                           critical_alerts=sum(open_alert_counts.values()))


@admissions_bp.route('/admissions')
@login_required
@roles_required(*STAFF)
@permissions_required('ADMISSION_VIEW')
def admissions():
    status = request.args.get('status', '').strip()
    query = Admission.query
    if status:
        query = query.filter(Admission.status == status)
    if current_user.has_role('Doctor') and not current_user.has_any_role('Admin', 'SuperAdmin'):
        from app.access import accessible_patient_ids
        pids = accessible_patient_ids(current_user)
        query = query.filter(Admission.patient_id.in_(sorted(pids) if pids else [-1]))
    items = query.order_by(Admission.admitted_at.desc()).limit(300).all()
    from app.models import User
    patients = Patient.query.join(User, Patient.user_id == User.id).order_by(User.full_name).all()
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
    if bed.ward_id != int(ward_id):
        flash('The selected bed does not belong to the selected ward.', 'danger')
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

    # Atomic bed claim: only one concurrent admit can flip Available->Occupied.
    from sqlalchemy import update as sql_update
    claimed = db.session.execute(
        sql_update(Bed).where(Bed.id == bed.id, Bed.status == 'Available')
        .values(status='Occupied'))
    if claimed.rowcount != 1:
        db.session.rollback()
        flash(f'Bed {bed.bed_no} was just taken by another admission. Choose another bed.', 'danger')
        return redirect(url_for('admissions.admissions'))
    db.session.refresh(bed)
    admission = Admission(
        patient_id=patient.id, ward_id=int(ward_id), bed_id=bed.id,
        admitting_doctor_id=int(doctor_id) if doctor_id else None,
        admitted_by=current_user.id, reason=reason,
        expected_discharge=expected_dt, status='Admitted')
    db.session.add(admission)
    db.session.flush()
    admission.admission_no = f'ADM-{1000 + admission.id}'
    log_activity('ADMIT_PATIENT', 'admission', admission.id,
                 f'patient={patient.id} bed={bed.id}')
    record_event(patient.id, 'ADMISSION',
                 f'Admitted to {bed.ward.name if bed.ward else "ward"} — Bed {bed.bed_no}',
                 f'{admission.admission_no} · {reason or "—"}',
                 source_type='admission', source_id=admission.id,
                 department='Admissions')
    from app.services.notifications import notify_patient, notify_role, notify
    notify_patient(patient, 'Admission confirmed',
                   f'You have been admitted to {bed.ward.name if bed.ward else "ward"} (bed {bed.bed_no}). Admission: {admission.admission_no}.',
                   entity_type='admission', entity_id=admission.id)
    notify_role('Nurse', f'New admission: {patient.user.full_name if patient.user else "patient"}',
                f'{bed.ward.name if bed.ward else "Ward"} · bed {bed.bed_no} · {reason or "no reason recorded"}',
                entity_type='admission', entity_id=admission.id)
    if admission.admitting_doctor and admission.admitting_doctor.user_id:
        notify(admission.admitting_doctor.user_id, f'Patient admitted under your care ({admission.admission_no})',
               f'{patient.user.full_name if patient.user else "Patient"} · {bed.ward.name if bed.ward else "Ward"} bed {bed.bed_no}',
               entity_type='admission', entity_id=admission.id)
    from app.services import tasks as task_svc
    task_svc.create_task(
        title=f'Admission nursing intake — {patient.user.full_name if patient.user else "patient"}',
        description=f'{admission.admission_no}: record admission vitals, nursing assessment and care plan.',
        task_type='NURSING', department='Nursing', patient_id=patient.id,
        assigned_role='Nurse', priority='HIGH',
        related_resource_type='admission', related_resource_id=admission.id)
    db.session.commit()
    flash(f'Patient admitted to bed {bed.bed_no} ({admission.admission_no}).', 'success')
    return redirect(url_for('admissions.dashboard'))


@admissions_bp.route('/admissions/<int:id>')
@login_required
@roles_required(*STAFF)
@permissions_required('ADMISSION_VIEW')
def view(id):
    """Inpatient episode detail: bed, physician, stay, discharge summary, bills."""
    admission = Admission.query.get_or_404(id)
    require_patient_access(admission.patient)
    from app.models import VitalSign, MedicationAdministration, NursingNote
    from app.services.patient_safety import patient_safety_context
    vitals = (VitalSign.query.filter_by(patient_id=admission.patient_id)
              .filter(VitalSign.recorded_at >= admission.admitted_at)
              .order_by(VitalSign.recorded_at.desc()).limit(10).all())
    doses = (MedicationAdministration.query.filter_by(patient_id=admission.patient_id)
             .filter(MedicationAdministration.created_at >= admission.admitted_at)
             .order_by(MedicationAdministration.scheduled_time.desc().nulls_last()).limit(15).all())
    notes = (NursingNote.query.filter_by(patient_id=admission.patient_id)
             .filter(NursingNote.created_at >= admission.admitted_at)
             .order_by(NursingNote.created_at.desc()).limit(10).all())
    bills = Bill.query.filter_by(patient_id=admission.patient_id, source_type='Room',
                                 source_id=admission.id).all()
    return render_template('admissions/view.html', title=f'Admission {admission.admission_no}',
                           admission=admission, patient=admission.patient,
                           vitals=vitals, doses=doses, notes=notes, bills=bills,
                           latest_vitals=vitals[0] if vitals else None,
                           active_admission=admission if admission.status == 'Admitted' else None,
                           **patient_safety_context(admission.patient_id),
                           today=utcnow().date())


@admissions_bp.route('/admissions/<int:id>/discharge', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'SuperAdmin', 'Doctor')
@permissions_required('ADMISSION_DISCHARGE')
def discharge(id):
    admission = Admission.query.get_or_404(id)
    require_patient_access(admission.patient)
    if admission.status != 'Admitted':
        flash('Admission is not active.', 'warning')
        return redirect(url_for('admissions.dashboard'))
    if request.method == 'GET':
        return render_template('admissions/discharge.html', title='Discharge Patient',
                               admission=admission)
    notes = request.form.get('discharge_notes')
    if not (request.form.get('discharge_diagnosis') or '').strip():
        flash('A discharge diagnosis is required to close the admission.', 'warning')
        return render_template('admissions/discharge.html', title='Discharge Patient',
                               admission=admission)
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
        bill = Bill.query.filter_by(source_type='Room', source_id=admission.id).first()
        if bill is None:
            bill = Bill(patient_id=admission.patient_id, source_type='Room',
                        source_id=admission.id, created_by=current_user.id)
            db.session.add(bill)
            db.session.flush()
            bill.bill_no = bill_number_for(bill.id)
            db.session.add(BillItem(bill_id=bill.id,
                                    description=f'{admission.ward.name} — {days} day(s)',
                                    quantity=1, unit_price=charge))
    log_activity('DISCHARGE_PATIENT', 'admission', admission.id,
                 f'notes={"yes" if notes else "no"}')
    record_event(admission.patient_id, 'DISCHARGE',
                 'Patient discharged',
                 f'{admission.days_stayed()} day(s) · {admission.discharge_summary[:140] if admission.discharge_summary else "—"}',
                 source_type='admission', source_id=admission.id,
                 department='Admissions')
    from app.services.notifications import notify_patient
    notify_patient(admission.patient, 'Discharged',
                   f'You have been discharged from {admission.ward.name if admission.ward else "the hospital"} '
                   f'after {admission.days_stayed()} day(s). Thank you for choosing us.',
                   entity_type='admission', entity_id=admission.id)
    from app.services import tasks as task_svc
    task_svc.complete_for_resource('admission', admission.id, 'Patient discharged')
    # Discharge follow-up becomes a tracked follow-up when instructions say so.
    follow_days = request.form.get('follow_up_days', type=int)
    if follow_days:
        from app.models import FollowUp
        from datetime import timedelta
        db.session.add(FollowUp(
            patient_id=admission.patient_id,
            provider_id=admission.admitting_doctor_id,
            scheduled_for=utcnow() + timedelta(days=follow_days),
            reason=f'Post-discharge review ({admission.admission_no})',
            status='Scheduled', created_by=current_user.id))
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
    require_patient_access(admission.patient)
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
