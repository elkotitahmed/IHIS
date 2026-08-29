from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime, date
from app import db
from app.models import (
    Appointment, DentalRecord, DentalChart, DentalProcedure,
    DentalImage, OrthodonticCase, Patient, Dentist,
)
from app.routes.decorators import roles_required, log_activity, save_upload

dentistry_bp = Blueprint('dentistry', __name__)


def _current_dentist():
    return Dentist.query.filter_by(user_id=current_user.id).first()


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


@dentistry_bp.route('/dashboard')
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
def dashboard():
    today_appointments = Appointment.query.count()
    pending_treatment_plans = DentalChart.query.filter(
        DentalChart.status.notin_(['Healthy', 'Missing'])).count()
    active_ortho_cases = OrthodonticCase.query.filter_by(status='Active').count()
    implant_cases = DentalChart.query.filter_by(status='Implant').count() + \
        DentalProcedure.query.filter(DentalProcedure.procedure_name.ilike('%implant%')).count()
    return render_template('dentistry/dashboard.html', title='Dentistry Dashboard',
                           today_appointments=today_appointments,
                           pending_treatment_plans=pending_treatment_plans,
                           active_ortho_cases=active_ortho_cases,
                           implant_cases=implant_cases)


@dentistry_bp.route('/patients')
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
def patients():
    all_patients = Patient.query.all()
    return render_template('dentistry/patients.html', title='Dental Patients',
                           patients=all_patients)


@dentistry_bp.route('/patients/<int:patient_id>/chart')
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
def chart(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    charts = DentalChart.query.filter_by(patient_id=patient.id).order_by(
        DentalChart.tooth_number.asc()).all()
    return render_template('dentistry/chart.html', title='Dental Chart',
                           patient=patient, charts=charts)


@dentistry_bp.route('/patients/<int:patient_id>/chart/add', methods=['POST'])
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
def add_chart(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    tooth = request.form.get('tooth_number')
    if not tooth:
        flash('Please select a tooth.', 'warning')
        return redirect(url_for('dentistry.chart', patient_id=patient.id))
    entry = DentalChart(
        patient_id=patient.id,
        tooth_number=tooth,
        numbering_system=request.form.get('numbering_system', 'FDI'),
        status=request.form.get('status', 'Healthy'),
        notes=request.form.get('notes'),
    )
    db.session.add(entry)
    log_activity('ADD_DENTAL_CHART', 'dental_chart', entry.id,
                 f"patient={patient.id} tooth={tooth}")
    db.session.commit()
    flash(f'Tooth {tooth} charted as {entry.status}.', 'success')
    return redirect(url_for('dentistry.chart', patient_id=patient.id))


@dentistry_bp.route('/patients/<int:patient_id>/record', methods=['GET', 'POST'])
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
def record(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    dental_record = DentalRecord.query.filter_by(patient_id=patient.id).first()

    if request.method == 'POST':
        if dental_record:
            dental_record.dental_history = request.form.get('dental_history')
            dental_record.dental_allergies = request.form.get('dental_allergies')
            dental_record.previous_procedures = request.form.get('previous_procedures')
        else:
            dental_record = DentalRecord(
                patient_id=patient.id,
                dental_history=request.form.get('dental_history'),
                dental_allergies=request.form.get('dental_allergies'),
                previous_procedures=request.form.get('previous_procedures'),
            )
            db.session.add(dental_record)
        log_activity('UPDATE_DENTAL_RECORD', 'dental_record', dental_record.id,
                     f"patient={patient.id}")
        db.session.commit()
        flash('Dental record saved.', 'success')
        return redirect(url_for('dentistry.record', patient_id=patient.id))

    return render_template('dentistry/dental_record.html', title='Dental Record',
                           patient=patient, dental_record=dental_record)


@dentistry_bp.route('/patients/<int:patient_id>/procedure', methods=['GET', 'POST'])
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
def procedures(patient_id):
    patient = Patient.query.get_or_404(patient_id)

    if request.method == 'POST':
        dentist = _current_dentist()
        cost = request.form.get('cost') or 0
        try:
            cost = float(cost)
        except ValueError:
            cost = 0.0
        procedure = DentalProcedure(
            patient_id=patient.id,
            dentist_id=dentist.id if dentist else None,
            procedure_name=request.form.get('procedure_name'),
            tooth_number=request.form.get('tooth_number'),
            cost=cost,
            notes=request.form.get('notes'),
        )
        db.session.add(procedure)
        log_activity('ADD_DENTAL_PROCEDURE', 'dental_procedure', procedure.id,
                     f"patient={patient.id} procedure={procedure.procedure_name}")
        db.session.commit()
        flash('Dental procedure recorded.', 'success')
        return redirect(url_for('dentistry.procedures', patient_id=patient.id))

    procedures_list = DentalProcedure.query.filter_by(patient_id=patient.id).order_by(
        DentalProcedure.performed_at.desc()).all()
    return render_template('dentistry/procedures.html', title='Dental Procedures',
                           patient=patient, procedures=procedures_list)


@dentistry_bp.route('/patients/<int:patient_id>/imaging', methods=['GET', 'POST'])
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
def imaging(patient_id):
    patient = Patient.query.get_or_404(patient_id)

    if request.method == 'POST':
        image_type = request.form.get('image_type')
        files = request.files.getlist('images')
        uploaded = 0
        for f in files:
            url = save_upload(f, 'dental_images', {'png', 'jpg', 'jpeg'})
            if url:
                image = DentalImage(
                    patient_id=patient.id,
                    image_type=image_type,
                    url=url,
                )
                db.session.add(image)
                log_activity('UPLOAD_DENTAL_IMAGE', 'dental_image', image.id,
                             f"patient={patient.id} url={url}")
                uploaded += 1
        db.session.commit()
        if uploaded:
            flash(f'{uploaded} image(s) uploaded.', 'success')
        else:
            flash('No valid images uploaded.', 'warning')
        return redirect(url_for('dentistry.imaging', patient_id=patient.id))

    images = DentalImage.query.filter_by(patient_id=patient.id).order_by(
        DentalImage.uploaded_at.desc()).all()
    return render_template('dentistry/imaging.html', title='Dental Imaging',
                           patient=patient, images=images)


@dentistry_bp.route('/ortho', methods=['GET', 'POST'])
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
def ortho():
    if request.method == 'POST':
        dentist = _current_dentist()
        progress = request.form.get('progress') or 0
        try:
            progress = int(progress)
        except ValueError:
            progress = 0
        case = OrthodonticCase(
            patient_id=request.form.get('patient_id'),
            dentist_id=dentist.id if dentist else None,
            case_type=request.form.get('case_type'),
            appliance=request.form.get('appliance'),
            start_date=_parse_date(request.form.get('start_date')) or date.today(),
            estimated_end_date=_parse_date(request.form.get('estimated_end_date')),
            status=request.form.get('status', 'Active'),
            progress=max(0, min(100, progress)),
            notes=request.form.get('notes'),
        )
        db.session.add(case)
        log_activity('CREATE_ORTHO_CASE', 'orthodontic_case', case.id,
                     f"patient={case.patient_id}")
        db.session.commit()
        flash('Orthodontic case created.', 'success')
        return redirect(url_for('dentistry.ortho'))

    cases = OrthodonticCase.query.order_by(
        OrthodonticCase.start_date.desc()).all()
    patients = Patient.query.all()
    return render_template('dentistry/ortho.html', title='Orthodontic Cases',
                           cases=cases, patients=patients)