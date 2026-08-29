from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime, date
from app import db
from app.models import (
    VitalSign, NursingNote, MedicationAdministration, CarePlan,
    Patient, Prescription, Medication, User,
)
from app.routes.decorators import roles_required, log_activity

nursing_bp = Blueprint('nursing', __name__)


def _as_int(value):
    try:
        return int(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _as_float(value):
    try:
        return float(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _as_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date() if value else None
    except (TypeError, ValueError):
        return None


def _is_abnormal(vital):
    if vital.temperature is not None and (vital.temperature > 38.0 or vital.temperature < 36.0):
        return True
    if vital.heart_rate is not None and (vital.heart_rate > 100 or vital.heart_rate < 60):
        return True
    if vital.respiratory_rate is not None and vital.respiratory_rate > 20:
        return True
    if vital.oxygen_saturation is not None and vital.oxygen_saturation < 90:
        return True
    if vital.blood_pressure_systolic is not None and \
            (vital.blood_pressure_systolic > 140 or vital.blood_pressure_systolic < 90):
        return True
    if vital.blood_pressure_diastolic is not None and \
            (vital.blood_pressure_diastolic > 90 or vital.blood_pressure_diastolic < 60):
        return True
    return False


@nursing_bp.route('/dashboard')
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
def dashboard():
    patients = Patient.query.all()
    assigned_patients = len(patients)
    med_count = MedicationAdministration.query.count()
    critical = 0
    for patient in patients:
        latest = VitalSign.query.filter_by(patient_id=patient.id).order_by(
            VitalSign.recorded_at.desc()).first()
        if latest and _is_abnormal(latest):
            critical += 1
    return render_template('nursing/dashboard.html', title='Nursing Dashboard',
                           assigned_patients=assigned_patients, med_count=med_count,
                           critical_alerts=critical, patients=patients)


@nursing_bp.route('/patients')
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
def patients():
    search = request.args.get('q', '')
    query = Patient.query
    if search:
        query = query.join(Patient.user).filter(
            db.or_(User.full_name.ilike(f'%{search}%'),
                   User.email.ilike(f'%{search}%')))
    results = query.limit(100).all()
    return render_template('nursing/patients.html', title='Nursing - Patients',
                           patients=results, search=search)


@nursing_bp.route('/patients/<int:patient_id>/vitals', methods=['GET', 'POST'])
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
def vitals(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if request.method == 'POST':
        vital = VitalSign(
            patient_id=patient.id,
            nurse_id=current_user.id,
            temperature=_as_float(request.form.get('temperature')),
            blood_pressure_systolic=_as_int(request.form.get('blood_pressure_systolic')),
            blood_pressure_diastolic=_as_int(request.form.get('blood_pressure_diastolic')),
            heart_rate=_as_int(request.form.get('heart_rate')),
            respiratory_rate=_as_int(request.form.get('respiratory_rate')),
            oxygen_saturation=_as_int(request.form.get('oxygen_saturation')),
            height_cm=_as_float(request.form.get('height_cm')),
            weight_kg=_as_float(request.form.get('weight_kg')),
        )
        db.session.add(vital)
        log_activity('CREATE_VITAL_SIGN', 'patient', patient.id,
                     f'Nurse {current_user.id} recorded vital signs')
        db.session.commit()
        flash('Vital signs recorded.', 'success')
        return redirect(url_for('nursing.vitals', patient_id=patient.id))
    vitals_list = VitalSign.query.filter_by(patient_id=patient.id).order_by(
        VitalSign.recorded_at.desc()).all()
    return render_template('nursing/vitals.html', title='Vital Signs',
                           patient=patient, vitals=vitals_list)


@nursing_bp.route('/patients/<int:patient_id>/notes', methods=['GET', 'POST'])
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
def notes(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if request.method == 'POST':
        note = NursingNote(
            patient_id=patient.id,
            nurse_id=current_user.id,
            note=request.form.get('note'),
            shift=request.form.get('shift'),
        )
        db.session.add(note)
        log_activity('CREATE_NURSING_NOTE', 'patient', patient.id,
                     f'Nurse {current_user.id} added a nursing note')
        db.session.commit()
        flash('Nursing note added.', 'success')
        return redirect(url_for('nursing.notes', patient_id=patient.id))
    notes_list = NursingNote.query.filter_by(patient_id=patient.id).order_by(
        NursingNote.created_at.desc()).all()
    return render_template('nursing/notes.html', title='Nursing Notes',
                           patient=patient, notes=notes_list)


@nursing_bp.route('/patients/<int:patient_id>/care-plan', methods=['GET', 'POST'])
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
def care_plan(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if request.method == 'POST':
        plan = CarePlan(
            patient_id=patient.id,
            nurse_id=current_user.id,
            title=request.form.get('title'),
            goals=request.form.get('goals'),
            interventions=request.form.get('interventions'),
            start_date=_as_date(request.form.get('start_date')) or date.today(),
            end_date=_as_date(request.form.get('end_date')),
        )
        db.session.add(plan)
        log_activity('CREATE_CARE_PLAN', 'patient', patient.id,
                     f'Nurse {current_user.id} created a care plan')
        db.session.commit()
        flash('Care plan created.', 'success')
        return redirect(url_for('nursing.care_plan', patient_id=patient.id))
    plans = CarePlan.query.filter_by(patient_id=patient.id).order_by(
        CarePlan.start_date.desc()).all()
    return render_template('nursing/care_plans.html', title='Care Plan',
                           patient=patient, plans=plans, today=date.today())


@nursing_bp.route('/medication-schedule')
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
def medication_schedule():
    records = MedicationAdministration.query.order_by(
        MedicationAdministration.administered_at.desc()).all()
    items = []
    for rec in records:
        patient = Patient.query.get(rec.patient_id)
        prescription = Prescription.query.get(rec.prescription_id) if rec.prescription_id else None
        line = prescription.items[0] if prescription and prescription.items else None
        medication = line.medication if line else None
        nurse = User.query.get(rec.nurse_id) if rec.nurse_id else None
        items.append({
            'record': rec,
            'patient': patient,
            'medication_name': medication.generic_name if medication else 'N/A',
            'dosage': line.dosage if line else None,
            'frequency': line.frequency if line else None,
            'nurse_name': nurse.full_name if nurse else 'N/A',
        })
    return render_template('nursing/med_schedule.html', title='Medication Schedule',
                           items=items)