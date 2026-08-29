from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime
from app import db
from app.models import (
    Doctor, Patient, Appointment, MedicalRecord, Diagnosis, Prescription,
    PrescriptionItem, Medication, LabOrder, LabTestCatalog, RadiologyOrder,
    ImagingType, Specialty, Referral, VitalSign, Notification, User,
)
from app.routes.decorators import roles_required, log_activity
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
    today = datetime.utcnow().date()
    todays_appts = [a for a in doctor.appointments
                    if a.scheduled_at and a.scheduled_at.date() == today]
    pending_labs = LabOrder.query.filter_by(status='Pending').count()
    pending_radio = RadiologyOrder.query.filter_by(status='Pending').count()
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
def patient_detail(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    records = MedicalRecord.query.filter_by(patient_id=patient.id).order_by(
        MedicalRecord.visit_date.desc()).all()
    diagnoses = Diagnosis.query.filter_by(patient_id=patient.id).all()
    vitals = VitalSign.query.filter_by(patient_id=patient.id).order_by(
        VitalSign.recorded_at.desc()).limit(10).all()
    return render_template('doctor/patient_detail.html', title='Patient Record',
                           patient=patient, records=records, diagnoses=diagnoses, vitals=vitals)


@doctor_bp.route('/patients/<int:patient_id>/emr/add', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
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


@doctor_bp.route('/patients/<int:patient_id>/prescriptions', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
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
        db.session.commit()
        flash('Prescription created.', 'success')
        return redirect(url_for('doctor.prescriptions', patient_id=patient.id))
    meds = Medication.query.all()
    prescriptions = Prescription.query.filter_by(patient_id=patient.id).all()
    return render_template('doctor/prescriptions.html', title='Prescriptions',
                           patient=patient, meds=meds, items=prescriptions)


@doctor_bp.route('/patients/<int:patient_id>/lab-order', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
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


@doctor_bp.route('/lab-results')
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def lab_results():
    doctor = _current_doctor()
    orders = LabOrder.query.filter_by(doctor_id=doctor.id).order_by(
        LabOrder.order_date.desc()).all() if doctor else []
    return render_template('doctor/lab_results.html', title='Lab Results', orders=orders)
