from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, current_app, abort
from flask_login import login_required, current_user
from datetime import datetime, date
import os
from app import db
from app.models import (
    Appointment, DentalRecord, DentalChart, DentalProcedure,
    DentalImage, OrthodonticCase, Patient, Dentist, DentalTreatmentPlan,
)
from app.routes.decorators import roles_required, log_activity, save_upload
from app.access import patient_access_required, require_patient_access

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
    today = date.today()
    today_appointments = Appointment.query.filter(
        db.func.date(Appointment.scheduled_at) == today).count()
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
@patient_access_required
def chart(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    charts = DentalChart.query.filter_by(patient_id=patient.id).order_by(
        DentalChart.tooth_number.asc()).all()
    return render_template('dentistry/chart.html', title='Dental Chart',
                           patient=patient, charts=charts)


@dentistry_bp.route('/patients/<int:patient_id>/chart/add', methods=['POST'])
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
@patient_access_required
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
    db.session.flush()
    log_activity('ADD_DENTAL_CHART', 'dental_chart', entry.id,
                 f"patient={patient.id} tooth={tooth}")
    db.session.commit()
    flash(f'Tooth {tooth} charted as {entry.status}.', 'success')
    return redirect(url_for('dentistry.chart', patient_id=patient.id))


@dentistry_bp.route('/patients/<int:patient_id>/record', methods=['GET', 'POST'])
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
@patient_access_required
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
        db.session.flush()
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
@patient_access_required
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
        db.session.flush()
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
@patient_access_required
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
                db.session.flush()
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


@dentistry_bp.route('/images/<int:image_id>/download')
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
def download_image(image_id):
    """Stream a stored dental image only to users with need-to-know access."""
    image = DentalImage.query.get_or_404(image_id)
    require_patient_access(image.patient)
    rel = (image.url or '').lstrip('/')
    if rel.startswith('static/uploads/'):
        rel = rel[len('static/uploads/'):]
        path = os.path.normpath(os.path.join(current_app.static_folder, 'uploads', rel))
    else:
        path = os.path.normpath(os.path.join(
            current_app.config.get('UPLOAD_FOLDER') or 'var/uploads', rel))
    if not os.path.isfile(path):
        abort(404)
    log_activity('DOWNLOAD_DENTAL_IMAGE', 'dental_image', image.id,
                 f'patient={image.patient_id}')
    db.session.commit()
    return send_file(path, as_attachment=True,
                     download_name=os.path.basename(path))


@dentistry_bp.route('/ortho', methods=['GET', 'POST'])
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
def ortho():
    if request.method == 'POST':
        pid = request.form.get('patient_id', type=int)
        p = Patient.query.get(pid) if pid else None
        if p is None:
            flash('Please select a valid patient.', 'warning')
            return redirect(url_for('dentistry.ortho'))
        require_patient_access(p)
        dentist = _current_dentist()
        progress = request.form.get('progress') or 0
        try:
            progress = int(progress)
        except ValueError:
            progress = 0
        case = OrthodonticCase(
            patient_id=p.id,
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
        db.session.flush()
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


@dentistry_bp.route('/patients/<int:patient_id>/treatment-plan', methods=['GET', 'POST'])
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
@patient_access_required
def treatment_plans(patient_id):
    patient = Patient.query.get_or_404(patient_id)

    if request.method == 'POST':
        dentist = _current_dentist()
        plan = DentalTreatmentPlan(
            patient_id=patient.id,
            dentist_id=dentist.id if dentist else None,
            title=request.form.get('title'),
            diagnosis=request.form.get('diagnosis'),
            status=request.form.get('status', 'Planned'),
            start_date=_parse_date(request.form.get('start_date')) or date.today(),
            end_date=_parse_date(request.form.get('end_date')),
            notes=request.form.get('notes'),
        )
        db.session.add(plan)
        db.session.flush()
        log_activity('CREATE_TREATMENT_PLAN', 'dental_treatment_plan', plan.id,
                     f'patient={patient.id}')
        db.session.commit()
        flash('Treatment plan created.', 'success')
        return redirect(url_for('dentistry.treatment_plans', patient_id=patient.id))

    plans = DentalTreatmentPlan.query.filter_by(patient_id=patient.id).order_by(
        DentalTreatmentPlan.start_date.desc()).all()
    return render_template('dentistry/treatment_plan.html', title='Treatment Plan',
                           patient=patient, plans=plans)


@dentistry_bp.route('/plans/<int:plan_id>/procedures', methods=['GET', 'POST'])
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
def plan_procedures(plan_id):
    plan = DentalTreatmentPlan.query.get_or_404(plan_id)
    require_patient_access(plan.patient)

    if request.method == 'POST':
        dentist = _current_dentist()
        cost = request.form.get('cost') or 0
        try:
            cost = float(cost)
        except ValueError:
            cost = 0.0
        procedure = DentalProcedure(
            patient_id=plan.patient_id,
            dentist_id=dentist.id if dentist else None,
            treatment_plan_id=plan.id,
            procedure_name=request.form.get('procedure_name'),
            tooth_number=request.form.get('tooth_number'),
            status=request.form.get('status', 'Planned'),
            scheduled_at=_parse_datetime(request.form.get('scheduled_at')) or None,
            cost=cost,
            materials=request.form.get('materials'),
            notes=request.form.get('notes'),
        )
        db.session.add(procedure)
        db.session.flush()
        log_activity('ADD_PLAN_PROCEDURE', 'dental_procedure', procedure.id,
                     f'patient={plan.patient_id} plan={plan.id}')
        db.session.commit()
        flash('Procedure added to plan.', 'success')
        return redirect(url_for('dentistry.plan_procedures', plan_id=plan.id))

    procedures = plan.procedures
    return render_template('dentistry/plan_procedures.html', title='Plan Procedures',
                           plan=plan, procedures=procedures)


@dentistry_bp.route('/procedures/<int:procedure_id>/status', methods=['POST'])
@login_required
@roles_required('Dentist', 'Admin', 'SuperAdmin')
def procedure_status(procedure_id):
    procedure = DentalProcedure.query.get_or_404(procedure_id)
    require_patient_access(procedure.patient)
    new_status = request.form.get('status')
    valid = {'Planned', 'Scheduled', 'InProgress', 'Completed', 'Cancelled'}
    if new_status not in valid:
        flash('Invalid status.', 'warning')
    else:
        procedure.status = new_status
        if new_status == 'Scheduled':
            procedure.scheduled_at = _parse_datetime(request.form.get('scheduled_at')) or \
                procedure.scheduled_at or datetime.now()
        elif new_status == 'Completed':
            procedure.completed_at = datetime.now()
            log_activity('COMPLETE_DENTAL_PROCEDURE', 'dental_procedure', procedure.id,
                         f'patient={procedure.patient_id}')
            from app.services.billing import ensure_bill_for_dental
            ensure_bill_for_dental(procedure.id)
            from app.services.notifications import notify_patient
            notify_patient(procedure.patient, 'Dental procedure completed',
                           f'{procedure.procedure_name or "Procedure"} completed.',
                           entity_type='dental_procedure', entity_id=procedure.id)
        log_activity('TRANSITION_DENTAL_PROCEDURE', 'dental_procedure', procedure.id,
                     f'to={new_status}')
        db.session.commit()
        flash(f'Procedure set to {new_status}.', 'success')
    if procedure.treatment_plan_id:
        return redirect(url_for('dentistry.plan_procedures', plan_id=procedure.treatment_plan_id))
    return redirect(url_for('dentistry.procedures', patient_id=procedure.patient_id))


def _parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return _parse_date_value(value)


def _parse_date_value(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None