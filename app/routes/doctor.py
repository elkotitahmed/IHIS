from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app import db
from app.models import (
    Doctor, Patient, Appointment, MedicalRecord, Diagnosis, Prescription,
    PrescriptionItem, Medication, LabOrder, LabTestCatalog, RadiologyOrder,
    ImagingType, Specialty, Referral, VitalSign, Notification, User, Bill,
)
from app.routes.decorators import roles_required, permissions_required, log_activity, log_change
from app.access import patient_access_required, require_patient_access, accessible_patient_ids
from app.utils import utcnow, is_clinical_locked
from app.services.ai import AIPatientRiskPrediction

doctor_bp = Blueprint('doctor', __name__)


def _current_doctor():
    return Doctor.query.filter_by(user_id=current_user.id).first()


@doctor_bp.route('/dashboard')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def dashboard():
    doctor = _current_doctor()
    if not doctor:
        flash('Doctor profile not found.', 'danger')
        return redirect(url_for('main.home'))
    today = utcnow().date()
    todays_appts = [a for a in doctor.appointments
                    if a.scheduled_at and a.scheduled_at.date() == today]
    pending_labs = LabOrder.query.filter_by(status='Pending', doctor_id=doctor.id).count()
    pending_radio = RadiologyOrder.query.filter_by(status='Pending', doctor_id=doctor.id).count()
    # Count patients currently appearing at risk (moderate or high) so the
    # "Critical Patients" KPI reflects real data instead of a hard-coded zero.
    risk_model = AIPatientRiskPrediction()
    critical_count = 0
    for p in Patient.query.limit(500).all():
        r = risk_model.predict_risk(p.id)
        if r.get('level') in ('Moderate', 'High'):
            critical_count += 1
    return render_template('doctor/dashboard.html', title='Doctor Dashboard',
                           doctor=doctor, todays_appts=todays_appts,
                           pending_labs=pending_labs, pending_radio=pending_radio,
                           critical_count=critical_count)


@doctor_bp.route('/patients')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def patients():
    search = request.args.get('q', '')
    query = Patient.query
    # A Doctor only sees patients they have a documented need-to-know
    # relationship with, so opening a patient's overview/detail never 403s.
    # Admin/SuperAdmin retain full supervisory search.
    if not (current_user.user_type == 'admin'
            and current_user.has_any_role('Admin', 'SuperAdmin')):
        allowed = accessible_patient_ids(current_user)
        query = query.filter(Patient.id.in_(allowed)) if allowed \
            else query.filter(Patient.id.is_(None))
    if search:
        query = query.join(Patient.user).filter(
            db.or_(User.full_name.ilike(f'%{search}%'),
                   User.email.ilike(f'%{search}%')))
    results = query.limit(100).all()
    return render_template('doctor/patients.html', title='Patient Search',
                           patients=results, search=search)


@doctor_bp.route('/patients/<int:patient_id>/overview')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def patient_overview(patient_id):
    """Patient 360 view: demographics, latest vitals, active problems,
    current medications, recent labs/imaging, and clinical alerts in one page."""
    patient = Patient.query.get_or_404(patient_id)

    latest_vitals = VitalSign.query.filter_by(patient_id=patient.id) \
        .order_by(VitalSign.recorded_at.desc()).first()
    vitals_history = VitalSign.query.filter_by(patient_id=patient.id) \
        .order_by(VitalSign.recorded_at.desc()).limit(5).all()

    diagnoses = Diagnosis.query.filter_by(patient_id=patient.id) \
        .order_by(Diagnosis.date_diagnosed.desc()).all()
    records = MedicalRecord.query.filter_by(patient_id=patient.id) \
        .order_by(MedicalRecord.visit_date.desc()).limit(5).all()
    active_rxs = Prescription.query.filter_by(patient_id=patient.id, status='Active') \
        .order_by(Prescription.prescribed_date.desc()).all()
    all_rxs = Prescription.query.filter_by(patient_id=patient.id) \
        .order_by(Prescription.prescribed_date.desc()).limit(10).all()

    recent_labs = LabOrder.query.filter_by(patient_id=patient.id) \
        .order_by(LabOrder.order_date.desc()).limit(5).all()
    recent_imaging = RadiologyOrder.query.filter_by(patient_id=patient.id) \
        .order_by(RadiologyOrder.order_date.desc()).limit(5).all()

    alerts = []
    if patient.allergies:
        alerts.append({'level': 'danger', 'label': 'Allergy',
                       'detail': patient.allergies})
    if patient.chronic_diseases:
        alerts.append({'level': 'warning', 'label': 'Chronic',
                       'detail': patient.chronic_diseases})
    if latest_vitals and latest_vitals.blood_pressure_systolic and \
            latest_vitals.blood_pressure_systolic >= 140:
        alerts.append({'level': 'danger', 'label': 'Elevated BP',
                       'detail': f"{latest_vitals.blood_pressure_systolic}/"
                                 f"{latest_vitals.blood_pressure_diastolic}"})
    abnormal_labs = LabOrder.query.filter(LabOrder.patient_id == patient.id,
                                          LabOrder.result.has(is_abnormal=True)).limit(5).all()
    for o in abnormal_labs:
        if o.result:
            alerts.append({'level': 'warning', 'label': 'Abnormal Lab',
                           'detail': f"{o.test.test_name}: {o.result.result_value}"})

    return render_template('doctor/patient_overview.html',
                           title='Patient Overview', patient=patient,
                           latest_vitals=latest_vitals, vitals_history=vitals_history,
                           diagnoses=diagnoses, records=records,
                           active_rxs=active_rxs, all_rxs=all_rxs,
                           recent_labs=recent_labs, recent_imaging=recent_imaging,
                           alerts=alerts)


@doctor_bp.route('/patients/<int:patient_id>')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def patient_detail(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    records = MedicalRecord.query.filter_by(patient_id=patient.id).order_by(
        MedicalRecord.visit_date.desc()).all()
    diagnoses = Diagnosis.query.filter_by(patient_id=patient.id).all()
    vitals = VitalSign.query.filter_by(patient_id=patient.id).order_by(
        VitalSign.recorded_at.desc()).limit(10).all()
    return render_template('doctor/patient_detail.html', title='Patient Record',
                            patient=patient, records=records, diagnoses=diagnoses, vitals=vitals)


@doctor_bp.route('/patients/<int:patient_id>/360')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def patient_360(patient_id):
    """Patient 360° — unified clinical view with alerts and a timeline."""
    patient = Patient.query.get_or_404(patient_id)

    latest_vitals = VitalSign.query.filter_by(patient_id=patient.id) \
        .order_by(VitalSign.recorded_at.desc()).first()
    diagnoses = Diagnosis.query.filter_by(patient_id=patient.id) \
        .order_by(Diagnosis.date_diagnosed.desc()).all()
    active_rxs = Prescription.query.filter_by(patient_id=patient.id, status='Active') \
        .order_by(Prescription.prescribed_date.desc()).all()
    recent_labs = LabOrder.query.filter_by(patient_id=patient.id) \
        .order_by(LabOrder.order_date.desc()).limit(8).all()
    recent_imaging = RadiologyOrder.query.filter_by(patient_id=patient.id) \
        .order_by(RadiologyOrder.order_date.desc()).limit(8).all()
    upcoming = Appointment.query.filter_by(
        patient_id=patient.id, status='Scheduled') \
        .order_by(Appointment.scheduled_at).limit(5).all()

    alerts = []
    if patient.allergies:
        alerts.append({'level': 'danger', 'label': 'Allergy', 'detail': patient.allergies})
    if patient.chronic_diseases:
        alerts.append({'level': 'warning', 'label': 'Chronic', 'detail': patient.chronic_diseases})
    if latest_vitals and latest_vitals.blood_pressure_systolic \
            and latest_vitals.blood_pressure_systolic >= 140:
        alerts.append({'level': 'danger', 'label': 'Elevated BP',
                       'detail': f"{latest_vitals.blood_pressure_systolic}/"
                                 f"{latest_vitals.blood_pressure_diastolic}"})
    for o in recent_labs:
        if o.result and o.result.is_abnormal:
            alerts.append({'level': 'warning', 'label': 'Abnormal Lab',
                           'detail': f"{o.test.test_name if o.test else 'Lab'}: "
                                     f"{o.result.result_value}"})

    # Each timeline entry carries (time, icon, label, detail, badge-color,
    # specialty-name). The color + specialty make clear which discipline owns
    # the event, which is the whole idea of a unified record over per-portal
    # silos.
    timeline = []
    for a in Appointment.query.filter_by(patient_id=patient.id).all():
        timeline.append((a.scheduled_at, 'calendar-check', 'Appointment',
                         a.reason or a.status, 'info', 'Reception'))
    for r in MedicalRecord.query.filter_by(patient_id=patient.id).all():
        timeline.append((r.visit_date, 'file-medical', 'Consultation',
                         r.diagnosis or '', 'primary', 'Doctor'))
    for d in diagnoses:
        timeline.append((d.date_diagnosed, 'stethoscope', 'Diagnosis',
                         d.description, 'primary', 'Doctor'))
    for rx in Prescription.query.filter_by(patient_id=patient.id).all():
        timeline.append((rx.prescribed_date, 'pills', 'Prescription',
                         f"{len(rx.items)} item(s) — {rx.status}", 'success', 'Pharmacy'))
    for o in recent_labs:
        if o.result:
            timeline.append((o.result.result_date, 'flask', 'Lab result',
                             o.test.test_name if o.test else 'Lab', 'warning', 'Laboratory'))
    for o in recent_imaging:
        if o.report:
            timeline.append((o.report.report_date, 'x-ray', 'Radiology',
                             o.imaging_type.name if o.imaging_type else 'Imaging',
                             'secondary', 'Radiology'))
    for v in VitalSign.query.filter_by(patient_id=patient.id).all():
        timeline.append((v.recorded_at, 'heartbeat', 'Vitals recorded',
                         '', 'info', 'Nursing'))

    bill_records = Bill.query.filter_by(patient_id=patient.id).all()
    for b in bill_records:
        timeline.append((
            b.issued_at, 'receipt', 'Bill',
            f'{b.bill_no} — {b.status} ({b.balance():.2f} remaining)',
            'danger' if b.status in ('Unpaid', 'PartiallyPaid') else 'success',
            'Billing'))
    bills_total = sum(b.total() for b in bill_records)
    bills_balance = sum(b.balance() for b in bill_records)

    timeline = [t for t in timeline if t[0]]
    timeline.sort(key=lambda x: x[0], reverse=True)

    return render_template(
        'doctor/patient_360.html', title='Patient 360', patient=patient,
        user=patient.user, latest_vitals=latest_vitals, diagnoses=diagnoses,
        active_rxs=active_rxs, recent_labs=recent_labs, recent_imaging=recent_imaging,
        upcoming=upcoming, alerts=alerts, timeline=timeline[:30],
        bills_total=bills_total, bills_balance=bills_balance)


@doctor_bp.route('/patients/<int:patient_id>/emr/add', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def add_emr(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    doctor = _current_doctor()
    if request.method == 'POST':
        record = MedicalRecord(
            patient_id=patient.id,
            doctor_id=doctor.id if doctor else None,
            diagnosis=request.form.get('diagnosis'),
            treatment_plan=request.form.get('treatment_plan'),
            clinical_notes=request.form.get('clinical_notes'),
        )
        db.session.add(record)
        if request.form.get('icd10') or request.form.get('diagnosis'):
            db.session.add(Diagnosis(
                patient_id=patient.id,
                doctor_id=doctor.id if doctor else None,
                icd10_code=request.form.get('icd10'),
                description=request.form.get('diagnosis') or 'Clinical note',
            ))
        log_activity('ADD_EMR', 'patient', patient.id)
        db.session.commit()
        flash('Medical record added.', 'success')
        return redirect(url_for('doctor.patient_detail', patient_id=patient.id))
    return render_template('doctor/add_emr.html', title='Add Medical Record', patient=patient)


@doctor_bp.route('/records/<int:record_id>/sign', methods=['POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@permissions_required('MEDICAL_RECORD_SIGN')
def sign_record(record_id):
    """Sign/lock a medical record. Signed records become immutable until amended."""
    record = MedicalRecord.query.get_or_404(record_id)
    require_patient_access(record.patient)
    if is_clinical_locked(record):
        flash('This record is already signed/locked.', 'info')
        return redirect(url_for('doctor.patient_detail', patient_id=record.patient_id))
    record.status = 'Signed'
    record.signed_by = current_user.id
    record.signed_at = utcnow()
    log_activity('SIGN_MEDICAL_RECORD', 'medical_record', record.id,
                 f'Signed by {current_user.full_name}')
    if record.patient:
        from app.services.notifications import notify_patient
        notify_patient(record.patient, 'Medical record signed',
                       'Your medical record has been signed by the attending physician.')
    db.session.commit()
    flash('Medical record signed and locked.', 'success')
    return redirect(url_for('doctor.patient_detail', patient_id=record.patient_id))


@doctor_bp.route('/records/<int:record_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@permissions_required('MEDICAL_RECORD_AMEND')
def edit_emr(record_id):
    """Edit an existing record; amending a signed record requires a reason and
    re-signing (clinical records are otherwise immutable)."""
    record = MedicalRecord.query.get_or_404(record_id)
    require_patient_access(record.patient)
    reason = request.form.get('reason') if request.method == 'POST' else None
    if request.method == 'POST' and is_clinical_locked(record) and not reason:
        flash('This record is signed and locked. Provide a reason to amend it.', 'warning')
        return redirect(url_for('doctor.edit_emr', record_id=record.id))
    if request.method == 'POST':
        old_state = {
            'diagnosis': record.diagnosis, 'treatment_plan': record.treatment_plan,
            'clinical_notes': record.clinical_notes, 'status': record.status,
        }
        record.diagnosis = request.form.get('diagnosis')
        record.treatment_plan = request.form.get('treatment_plan')
        record.clinical_notes = request.form.get('clinical_notes')
        if is_clinical_locked(record):
            record.status = 'Draft'  # must be re-signed after amendment
            record.signed_by = None
            record.signed_at = None
            log_change('AMEND_MEDICAL_RECORD', 'medical_record', record.id,
                       old_value=old_state,
                       new_value={'diagnosis': record.diagnosis,
                                  'treatment_plan': record.treatment_plan,
                                  'clinical_notes': record.clinical_notes,
                                  'status': record.status},
                       reason=reason or 'No reason provided',
                       details=f'patient={record.patient_id}')
            flash('Amendment recorded; record reopened for re-signing.', 'success')
        else:
            log_activity('EDIT_MEDICAL_RECORD', 'medical_record', record.id)
            flash('Medical record updated.', 'success')
        db.session.commit()
        return redirect(url_for('doctor.patient_detail', patient_id=record.patient_id))
    return render_template('doctor/edit_emr.html', title='Edit Medical Record',
                           patient=record.patient, record=record)


@doctor_bp.route('/patients/<int:patient_id>/prescriptions', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def prescriptions(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    doctor = _current_doctor()
    if request.method == 'POST':
        rx = Prescription(
            patient_id=patient.id,
            doctor_id=doctor.id if doctor else None,
            refills=int(request.form.get('refills') or 0),
        )
        db.session.add(rx)
        db.session.flush()

        med_ids = request.form.getlist('medication_id')
        dosages = request.form.getlist('dosage')
        frequencies = request.form.getlist('frequency')
        durations = request.form.getlist('duration')
        instructions = request.form.getlist('instructions')
        quantities = request.form.getlist('quantity')

        added = 0
        for i, mid in enumerate(med_ids):
            if not mid:
                continue
            qty = quantities[i] if i < len(quantities) else 1
            try:
                qty = max(1, int(qty))
            except (TypeError, ValueError):
                qty = 1
            db.session.add(PrescriptionItem(
                prescription_id=rx.id,
                medication_id=int(mid),
                dosage=dosages[i] if i < len(dosages) else '',
                frequency=frequencies[i] if i < len(frequencies) else '',
                duration=durations[i] if i < len(durations) else '',
                instructions=instructions[i] if i < len(instructions) else '',
                quantity=qty,
            ))
            added += 1

        if added == 0:
            db.session.rollback()
            flash('Add at least one medication to the prescription.', 'danger')
            return redirect(url_for('doctor.prescriptions', patient_id=patient.id))

        log_activity('CREATE_PRESCRIPTION', 'patient', patient.id,
                     f'items={added}')
        # Route the prescription into the pharmacy work queue (commit together
        # so the task is never rolled back by a notification failure).
        task = None
        from app.services import tasks as task_svc
        task = task_svc.create_task(
            title=f'Dispense prescription #{rx.id}',
            description=f'Review and dispense {added} item(s). Check interactions and stock.',
            task_type='PHARMACY', department='Pharmacy',
            patient_id=patient.id, assigned_role='Pharmacist',
            priority='NORMAL', related_resource_type='prescription',
            related_resource_id=rx.id)
        db.session.commit()
        try:
            task_svc.notify_task_activity(task)
            from app.services.notifications import notify_role
            notify_role('Pharmacist',
                        f'New prescription #{rx.id}',
                        f'A new prescription with {added} item(s) was written for patient #{patient.id}.',
                        entity_type='prescription', entity_id=rx.id)
            db.session.commit()
        except Exception:
            db.session.rollback()
            db.session.commit()
        flash('Prescription created.', 'success')
        return redirect(url_for('doctor.prescriptions', patient_id=patient.id))
    meds = Medication.query.all()
    prescriptions = Prescription.query.filter_by(patient_id=patient.id).all()
    return render_template('doctor/prescriptions.html', title='Prescriptions',
                           patient=patient, meds=meds, items=prescriptions)


@doctor_bp.route('/prescriptions/<int:rx_id>/cancel', methods=['POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@permissions_required('PRESCRIPTION_CREATE')
def cancel_prescription(rx_id):
    """Cancel an Active prescription. Once any item is dispensed it becomes
    locked and can no longer be cancelled (must go through the pharmacy)."""
    rx = Prescription.query.get_or_404(rx_id)
    require_patient_access(rx.patient)
    if rx.status == 'Cancelled':
        flash('Prescription already cancelled.', 'info')
    elif rx.status == 'Dispensed' or rx.dispensed():
        flash('Cannot cancel: medication has already been dispensed.', 'danger')
    else:
        old = rx.status
        rx.status = 'Cancelled'
        for item in rx.items:
            if item.status != 'Dispensed':
                item.status = 'Cancelled'
        log_change('CANCEL_PRESCRIPTION', 'prescription', rx.id,
                   old_value={'status': old}, new_value={'status': 'Cancelled'},
                   reason=request.form.get('reason') or 'Cancelled by physician',
                   details=f'patient={rx.patient_id}')
        db.session.commit()
        flash('Prescription cancelled.', 'success')
    return redirect(url_for('doctor.prescriptions', patient_id=rx.patient_id))


@doctor_bp.route('/patients/<int:patient_id>/lab-order', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def lab_order(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    doctor = _current_doctor()
    if request.method == 'POST':
        db.session.add(LabOrder(
            patient_id=patient.id,
            doctor_id=doctor.id if doctor else None,
            test_id=request.form.get('test_id'),
            priority=request.form.get('priority', 'Normal'),
            notes=request.form.get('notes'),
        ))
        log_activity('REQUEST_LAB', 'patient', patient.id)
        db.session.commit()
        flash('Lab order requested.', 'success')
        return redirect(url_for('doctor.patient_detail', patient_id=patient.id))
    tests = LabTestCatalog.query.filter_by(is_active=True).all()
    return render_template('doctor/lab_order.html', title='Lab Order', patient=patient, tests=tests)


@doctor_bp.route('/patients/<int:patient_id>/radiology-order', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
@patient_access_required
def radiology_order(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    doctor = _current_doctor()
    if request.method == 'POST':
        db.session.add(RadiologyOrder(
            patient_id=patient.id,
            doctor_id=doctor.id if doctor else None,
            imaging_type_id=request.form.get('imaging_type_id'),
            priority=request.form.get('priority', 'Normal'),
            notes=request.form.get('notes'),
        ))
        log_activity('REQUEST_RADIOLOGY', 'patient', patient.id)
        db.session.commit()
        flash('Radiology order requested.', 'success')
        return redirect(url_for('doctor.patient_detail', patient_id=patient.id))
    imaging = ImagingType.query.all()
    return render_template('doctor/radiology_order.html', title='Radiology Order',
                           patient=patient, imaging=imaging)


@doctor_bp.route('/appointments')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def appointments():
    doctor = _current_doctor()
    items = Appointment.query.filter_by(doctor_id=doctor.id).order_by(
        Appointment.scheduled_at.desc()).all() if doctor else []
    return render_template('doctor/appointments.html', title='My Appointments', items=items)


@doctor_bp.route('/appointments/<int:appt_id>/complete', methods=['POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def complete_appointment(appt_id):
    """Close out a visit: mark the appointment Completed or NoShow.

    When Completed, the consultation fee is pushed into billing automatically
    (one bill per appointment) so front desk collects on the visit."""
    appt = Appointment.query.get_or_404(appt_id)
    mode = request.form.get('mode', 'Completed')
    if mode not in ('Completed', 'NoShow'):
        mode = 'Completed'
    appt.status = mode
    log_activity('COMPLETE_APPOINTMENT', 'appointment', appt.id,
                 f'mode={mode} patient={appt.patient_id}')
    if mode == 'Completed':
        from app.services.billing import ensure_bill_for_consultation
        bill = ensure_bill_for_consultation(appt.id, appt.patient_id, appt.doctor_id)
        if bill:
            log_activity('AUTO_BILL_CONSULTATION', 'bill', bill.id,
                         f'appointment={appt.id} total={bill.total():.2f}')
    db.session.commit()
    if mode == 'Completed':
        flash('Appointment marked completed. Consultation bill generated.', 'success')
    else:
        flash('Appointment marked as no-show.', 'warning')
    return redirect(url_for('doctor.appointments'))


@doctor_bp.route('/lab-results')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def lab_results():
    doctor = _current_doctor()
    orders = LabOrder.query.filter_by(doctor_id=doctor.id).order_by(
        LabOrder.order_date.desc()).all() if doctor else []
    return render_template('doctor/lab_results.html', title='Lab Results', orders=orders)
