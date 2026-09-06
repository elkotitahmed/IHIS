from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, send_file, current_app
from flask_login import login_required, current_user
import os
from app import db
from app.models import RadiologyOrder, RadiologyReport, ImagingType, Patient, User
from app.routes.decorators import roles_required, permissions_required, log_activity, log_change, save_upload
from app.access import require_patient_access, accessible_patient_ids
from app.utils import utcnow, is_clinical_locked
from app.services.status import assert_transition, StatusTransitionError
from app.services.notifications import (notify_doctor, notify_patient, notify_role,
                                        notify_ordering_clinicians)
from app.services import tasks as task_svc
from app.services.timeline import record_event
from app.services import alerts as alert_svc
from app.services.radiology.safety_service import RadiologySafetyService

radiology_bp = Blueprint('radiology', __name__)

# Who may *run* a study (technologist duties) vs. who may *report* it.
RAD_STAFF = ('Radiologist', 'RadiologyTechnician', 'Admin', 'SuperAdmin')
RAD_VIEWERS = ('Radiologist', 'RadiologyTechnician', 'Doctor', 'Admin', 'SuperAdmin')


def _order(oid):
    return db.session.get(RadiologyOrder, oid) or abort(404)


def _report_of(order):
    return RadiologyReport.query.filter_by(order_id=order.id).first()


def _status_badge(s):
    return {
        'Pending': 'warning', 'Scheduled': 'primary', 'Arrived': 'info',
        'InProgress': 'secondary', 'Performed': 'secondary',
        'Reported': 'success', 'Signed': 'success', 'Finalized': 'success',
        'Cancelled': 'danger',
    }.get(s, 'secondary')


@radiology_bp.route('/dashboard')
@login_required
@roles_required(*RAD_VIEWERS)
def dashboard():
    pending = RadiologyOrder.query.filter_by(status='Pending').count()
    scheduled = RadiologyOrder.query.filter_by(status='Scheduled').count()
    in_progress = RadiologyOrder.query.filter(RadiologyOrder.status.in_(
        ('Arrived', 'InProgress', 'Performed'))).count()
    completed = RadiologyOrder.query.filter(RadiologyOrder.status.in_(
        ('Reported', 'Signed', 'Finalized'))).count()
    recent = RadiologyOrder.query.order_by(RadiologyOrder.order_date.desc()).limit(10).all()
    tasks = task_svc.department_queue('Radiology', ('NEW', 'ASSIGNED', 'IN_PROGRESS', 'ON_HOLD'))
    return render_template('radiology/dashboard.html', title='Radiology Dashboard',
                           pending=pending, scheduled=scheduled, in_progress=in_progress,
                           completed=completed, recent=recent, tasks=tasks,
                           status_badge=_status_badge)


@radiology_bp.route('/orders')
@login_required
@roles_required(*RAD_VIEWERS)
def orders():
    q = RadiologyOrder.query
    f_status = request.args.get('status', '').strip()
    search = request.args.get('q', '').strip()
    if f_status:
        q = q.filter(RadiologyOrder.status == f_status)
    if search:
        numeric = int(search) if search.isdigit() else -1
        q = (q.join(Patient, RadiologyOrder.patient_id == Patient.id)
              .join(User, Patient.user_id == User.id)
              .filter(db.or_(RadiologyOrder.id == numeric,
                             User.full_name.ilike(f'%{search}%'),
                             Patient.mrn.ilike(f'%{search}%'))))
    # A referring doctor only sees studies they ordered; radiology staff and
    # supervisors see the full worklist.
    if current_user.has_role('Doctor') and not current_user.has_any_role(*RAD_STAFF):
        doc = current_user.doctor_profile
        q = q.filter(RadiologyOrder.doctor_id == (doc.id if doc else -1))
    all_orders = q.order_by(RadiologyOrder.order_date.desc()).limit(300).all()
    can_report = current_user.has_any_role('Radiologist', 'Admin', 'SuperAdmin')
    can_perform = current_user.has_any_role(*RAD_STAFF)
    return render_template('radiology/orders.html', title='Radiology Orders',
                           orders=all_orders, status_badge=_status_badge,
                           f_status=f_status, search=search,
                           can_report=can_report, can_perform=can_perform)


@radiology_bp.route('/order/new', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def new_order():
    if request.method == 'POST':
        try:
            patient_id = int(request.form.get('patient_id'))
            imaging_type_id = int(request.form.get('imaging_type_id'))
        except (TypeError, ValueError):
            flash('Please select a valid patient and imaging type.', 'danger')
            return redirect(url_for('radiology.new_order'))
        if not Patient.query.get(patient_id) or not ImagingType.query.get(imaging_type_id):
            flash('Please select a valid patient and imaging type.', 'danger')
            return redirect(url_for('radiology.new_order'))
        patient = db.session.get(Patient, patient_id)
        require_patient_access(patient)
        from app.services.clinical_orders import create_radiology_order
        order = create_radiology_order(
            patient, current_user.doctor_profile, imaging_type_id,
            priority=request.form.get('priority', 'Normal'),
            notes=request.form.get('notes'))
        log_activity('CREATE_RADIOLOGY_ORDER', 'radiology_order', order.id,
                     f'patient={patient_id}')
        db.session.commit()
        # --- Safety evaluation for the imaging type ---
        safety_svc = RadiologySafetyService()
        imaging_name = (order.imaging_type.name if order.imaging_type else '').lower()
        safety_warnings = []
        if 'ct' in imaging_name or 'computed tomography' in imaging_name:
            ct_eval = safety_svc.evaluate_ct_safety(patient_id)
            safety_warnings = ct_eval.get('warnings', [])
        elif 'mri' in imaging_name or 'magnetic' in imaging_name:
            mri_eval = safety_svc.evaluate_mri_safety(patient_id)
            safety_warnings = mri_eval.get('warnings', [])
        for sw in safety_warnings:
            if sw.get('severity') in ('Critical', 'Important'):
                flash(f"Safety: {sw.get('title', '')} — {sw.get('message', '')}", 'warning')
        flash('Radiology order created; study workflow started.', 'success')
        return redirect(url_for('radiology.orders'))

    patients = Patient.query.filter(
        Patient.id.in_(accessible_patient_ids(current_user) or [-1])
    ).order_by(Patient.id).all()
    imaging = ImagingType.query.all()
    return render_template('radiology/new_order.html', title='New Radiology Order',
                           patients=patients, imaging=imaging)


@radiology_bp.route('/orders/<int:order_id>/schedule', methods=['POST'])
@login_required
@roles_required(*RAD_STAFF)
def schedule(order_id):
    order = _order(order_id)
    try:
        assert_transition('radiology_order', order, 'Scheduled')
    except StatusTransitionError as e:
        flash(str(e), 'danger'); db.session.rollback()
        return redirect(url_for('radiology.orders'))
    order.status = 'Scheduled'
    from datetime import datetime
    dt_str = request.form.get('scheduled_at')
    if dt_str:
        try:
            order.scheduled_at = datetime.strptime(dt_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            pass
    log_activity('SCHEDULE_RADIOLOGY', 'radiology_order', order.id)
    db.session.commit()
    flash('Study scheduled.', 'success')
    return redirect(url_for('radiology.orders'))


@radiology_bp.route('/orders/<int:order_id>/arrive', methods=['POST'])
@login_required
@roles_required(*RAD_STAFF)
def arrive(order_id):
    order = _order(order_id)
    try:
        assert_transition('radiology_order', order, 'Arrived')
    except StatusTransitionError as e:
        flash(str(e), 'danger'); db.session.rollback()
        return redirect(url_for('radiology.orders'))
    order.status = 'Arrived'
    order.arrived_at = utcnow()
    log_activity('RADIOLOGY_ARRIVED', 'radiology_order', order.id)
    db.session.commit()
    flash('Patient arrived; ready to capture.', 'success')
    return redirect(url_for('radiology.orders'))


@radiology_bp.route('/orders/<int:order_id>/perform', methods=['POST'])
@login_required
@roles_required(*RAD_STAFF)
def perform(order_id):
    order = _order(order_id)
    try:
        assert_transition('radiology_order', order, 'Performed')
    except StatusTransitionError as e:
        flash(str(e), 'danger'); db.session.rollback()
        return redirect(url_for('radiology.orders'))
    order.status = 'Performed'
    order.performed_by = current_user.id
    order.performed_at = utcnow()
    order.scanned_at = utcnow()
    order.technical_notes = request.form.get('technical_notes') or order.technical_notes
    log_activity('RADIOLOGY_PERFORMED', 'radiology_order', order.id,
                 f'by uid={current_user.id}')
    record_event(order.patient_id, 'RADIOLOGY',
                 f'Study performed: {order.imaging_type.name if order.imaging_type else "Imaging"}',
                 f'Acquired by {current_user.full_name}',
                 source_type='radiology_order', source_id=order.id, department='Radiology')
    # Hand over to the reporting radiologist.
    task_svc.complete_for_resource('radiology_order', order.id, 'Study acquired')
    task_svc.create_task(
        title=f'Report study #{order.id}: {order.imaging_type.name if order.imaging_type else ""}',
        description='Review the images and author/sign the diagnostic report.',
        task_type='RADIOLOGY', department='Radiology', patient_id=order.patient_id,
        assigned_role='Radiologist', priority=str(order.priority or 'NORMAL').upper(),
        related_resource_type='radiology_report_task', related_resource_id=order.id)
    notify_role('Radiologist', f'Study #{order.id} ready for reporting',
                f'{order.imaging_type.name if order.imaging_type else "Study"} has been performed and awaits a report.',
                entity_type='radiology_order', entity_id=order.id)
    db.session.commit()
    flash('Study performed; ready for reporting.', 'success')
    return redirect(url_for('radiology.orders'))


@radiology_bp.route('/orders/<int:order_id>/upload', methods=['POST'])
@login_required
@roles_required(*RAD_STAFF)
def upload_images(order_id):
    order = _order(order_id)
    files = request.files.getlist('images')
    urls = []
    for f in files:
        url = save_upload(f, 'radiology_images', {'png', 'jpg', 'jpeg', 'dcm'})
        if url:
            urls.append(url)
    if urls:
        existing = order.image_urls or ''
        order.image_urls = (existing + ',' + ','.join(urls)).strip(',')
        if order.status in ('Pending', 'Scheduled', 'Arrived'):
            order.status = 'InProgress'
        db.session.commit()
        flash('Images uploaded successfully.', 'success')
    else:
        flash('No valid images uploaded.', 'warning')
    return redirect(url_for('radiology.orders'))


@radiology_bp.route('/orders/<int:order_id>/images/<int:index>/download')
@login_required
def download_image(order_id, index):
    """Stream a stored study image to users with need-to-know access."""
    order = _order(order_id)
    require_patient_access(order.patient)
    urls = [u for u in (order.image_urls or '').split(',') if u]
    if not urls or index < 0 or index >= len(urls):
        abort(404)
    rel = urls[index].lstrip('/')
    if rel.startswith('static/uploads/'):
        rel = rel[len('static/uploads/'):]
        path = os.path.normpath(os.path.join(current_app.static_folder, 'uploads', rel))
    else:
        path = os.path.normpath(os.path.join(
            current_app.config.get('UPLOAD_FOLDER') or 'var/uploads', rel))
    if not os.path.isfile(path):
        abort(404)
    log_activity('DOWNLOAD_RADIOLOGY_IMAGE', 'radiology_order', order.id,
                 f'patient={order.patient_id}')
    db.session.commit()
    return send_file(path, as_attachment=True,
                     download_name=os.path.basename(path))


@radiology_bp.route('/orders/<int:order_id>/report', methods=['GET', 'POST'])
@login_required
@roles_required('Radiologist', 'Admin', 'SuperAdmin')
@permissions_required('RADIOLOGY_CREATE')
def enter_report(order_id):
    order = _order(order_id)
    report = _report_of(order)

    if request.method == 'POST' and report and is_clinical_locked(report) and not request.form.get('amend'):
        flash('This report is signed and locked. Use "Amend Report" to correct it with a reason.', 'warning')
        return redirect(url_for('radiology.enter_report', order_id=order.id))

    if request.method == 'POST':
        findings = request.form.get('findings')
        impression = request.form.get('impression')
        recommendation = request.form.get('recommendation')
        reason = request.form.get('reason')

        if report and report.status in ('Signed', 'Locked', 'Finalized'):
            old_state = {
                'findings': report.findings,
                'impression': report.impression,
                'recommendation': report.recommendation,
                'status': report.status,
            }
            # Preserve the signed text verbatim before it is replaced.
            from app.models import RadiologyReportVersion
            version_no = (db.session.query(db.func.count(RadiologyReportVersion.id))
                          .filter_by(report_id=report.id).scalar() or 0) + 1
            db.session.add(RadiologyReportVersion(
                report_id=report.id, version_number=version_no,
                findings=report.findings, impression=report.impression,
                recommendation=report.recommendation,
                changed_by=current_user.id, change_reason=reason or 'No reason provided'))
            report.amended_from_id = report.id
            report.findings = findings
            report.impression = impression
            report.recommendation = recommendation
            report.reported_by = current_user.id
            report.report_date = utcnow()
            report.status = 'Draft'
            new_state = {
                'findings': report.findings,
                'impression': report.impression,
                'recommendation': report.recommendation,
                'status': report.status,
            }
            log_change('AMEND_RADIOLOGY_REPORT', 'radiology_report', report.id,
                       old_value=old_state, new_value=new_state,
                       reason=reason or 'No reason provided',
                       details=f'order={order.id}')
            flash('Amendment recorded and sent for re-signing.', 'success')
        elif report:
            report.findings = findings
            report.impression = impression
            report.recommendation = recommendation
            report.reported_by = current_user.id
            report.report_date = utcnow()
        else:
            report = RadiologyReport(
                order_id=order.id, findings=findings, impression=impression,
                recommendation=recommendation, reported_by=current_user.id)
            db.session.add(report)

        if order.status not in ('Reported', 'Signed', 'Finalized'):
            order.status = 'Reported'
        log_activity('ENTER_RADIOLOGY_REPORT', 'radiology_order', order.id)
        record_event(order.patient_id, 'RADIOLOGY',
                     f'Report entered: {order.imaging_type.name if order.imaging_type else "Study"}',
                     f'Impression: {impression or ""}',
                     source_type='radiology_order', source_id=order.id,
                     department='Radiology')
        # Critical-finding engine: rules (authoritative) + local classifier +
        # radiologist flag -> alert, urgent task, notifications, audit.
        from app.services import radiology_critical as rc
        manual_critical = bool(request.form.get('critical_finding'))
        evaluation = rc.evaluate_report(order, report, manual_text=request.form.get('critical_finding_text'),
                                        manual=manual_critical)
        rc.raise_alert(order, report, evaluation, actor_id=current_user.id)
        db.session.commit()
        return redirect(url_for('radiology.orders'))

    return render_template('radiology/report.html', title='Enter Radiology Report',
                           order=order, report=report, status_badge=_status_badge)


@radiology_bp.route('/orders/<int:order_id>/sign', methods=['POST'])
@login_required
@roles_required('Radiologist', 'Admin', 'SuperAdmin')
@permissions_required('RADIOLOGY_SIGN')
def sign_report(order_id):
    order = _order(order_id)
    report = _report_of(order)
    if not report:
        flash('No report to sign. Enter the report first.', 'warning')
        return redirect(url_for('radiology.enter_report', order_id=order.id))
    if is_clinical_locked(report):
        flash('Report is already signed/locked.', 'info')
        return redirect(url_for('radiology.enter_report', order_id=order.id))
    report.status = 'Signed'
    report.signed_by = current_user.id
    log_activity('SIGN_RADIOLOGY_REPORT', 'radiology_report', report.id,
                 f'Signed by {current_user.full_name}')
    if order.status not in ('Signed', 'Finalized'):
        order.status = 'Signed'
    record_event(order.patient_id, 'RADIOLOGY',
                 f'Report signed: {order.imaging_type.name if order.imaging_type else "Study"}',
                 f'Impression: {(report.impression or "")[:140]}',
                 source_type='radiology_order', source_id=order.id, department='Radiology')
    if order.imaging_type and order.imaging_type.price:
        from app.services.billing import ensure_bill_for_radiology
        ensure_bill_for_radiology(order.id)
    notify_patient(order.patient, 'Radiology report ready',
                   f'Your radiology report ({order.imaging_type.name if order.imaging_type else ""}) has been signed and is available.',
                   entity_type='radiology_order', entity_id=order.id)
    notify_ordering_clinicians(order, f'Radiology report ready — order #{order.id}',
                               f'The report for "{order.imaging_type.name if order.imaging_type else ""}" has been signed.',
                               entity_type='radiology_order', entity_id=order.id)
    task_svc.complete_for_resource('radiology_order', order.id, 'Report signed')
    task_svc.complete_for_resource('radiology_report_task', order.id, 'Report signed')
    # Re-evaluate at signing so an amended/signed report can never bypass the
    # critical-finding workflow (idempotent per report).
    from app.services import radiology_critical as rc
    rc.raise_alert(order, report, rc.evaluate_report(order, report), actor_id=current_user.id)
    db.session.commit()
    flash('Radiology report signed and locked.', 'success')
    return redirect(url_for('radiology.enter_report', order_id=order.id))


@radiology_bp.route('/orders/<int:order_id>/cancel', methods=['POST'])
@login_required
@roles_required('Radiologist', 'Doctor', 'Admin', 'SuperAdmin')
def cancel_order(order_id):
    order = _order(order_id)
    try:
        assert_transition('radiology_order', order, 'Cancelled')
    except StatusTransitionError as e:
        flash(str(e), 'danger'); db.session.rollback()
        return redirect(url_for('radiology.orders'))
    order.status = 'Cancelled'
    log_activity('CANCEL_RADIOLOGY_ORDER', 'radiology_order', order.id)
    task_svc.cancel_for_resource('radiology_order', order.id, 'Imaging order cancelled')
    record_event(order.patient_id, 'RADIOLOGY', 'Imaging order cancelled',
                 order.imaging_type.name if order.imaging_type else 'Study',
                 source_type='radiology_order', source_id=order.id, department='Radiology')
    if order.doctor:
        notify_doctor(order.doctor, f'Radiology order #{order.id} cancelled',
                      f'The study "{order.imaging_type.name if order.imaging_type else ""}" was cancelled.',
                      entity_type='radiology_order', entity_id=order.id)
    db.session.commit()
    flash('Radiology order cancelled.', 'success')
    return redirect(url_for('radiology.orders'))


# ---------------------------------------------------------------------------
# RADIATION DOSE & IMAGING SAFETY ROUTES
# ---------------------------------------------------------------------------

@radiology_bp.route('/dose-dashboard')
@login_required
@roles_required(*RAD_VIEWERS)
def dose_dashboard():
    """Radiation dose dashboard — annual exposure summary + alerts."""
    from app.services.radiology.dose_service import RadiationDoseService
    dose_svc = RadiationDoseService()
    pid = request.args.get('patient_id', type=int)
    patient = None
    annual = None
    cumulative = None
    alerts = []
    if pid:
        patient = db.session.get(Patient, pid)
        if patient:
            require_patient_access(patient)
            annual = dose_svc.get_patient_annual_summary(pid)
            cumulative = dose_svc.get_patient_cumulative_summary(pid)
            alerts = dose_svc.generate_dose_alerts(pid)
    patients = Patient.query.all() if current_user.has_any_role('Admin', 'SuperAdmin') else []
    return render_template('radiology/dose_dashboard.html',
                           title='Radiation Dose Dashboard',
                           patient=patient, annual=annual,
                           cumulative=cumulative, alerts=alerts,
                           patients=patients, selected_pid=pid)


@radiology_bp.route('/safety-profile/<int:patient_id>')
@login_required
@roles_required('Radiologist', 'RadiologyTechnician', 'Doctor', 'Nurse', 'Admin', 'SuperAdmin')
def safety_profile(patient_id):
    """Patient imaging safety profile — MRI implants, contrast history, renal, pregnancy."""
    from app.services.radiology.safety_service import RadiologySafetyService
    from app.services.radiology.dose_service import RadiationDoseService
    patient = db.session.get(Patient, patient_id) or abort(404)
    require_patient_access(patient)
    safety_svc = RadiologySafetyService()
    profile = safety_svc.get_or_create_safety_profile(patient_id)
    ct_eval = safety_svc.evaluate_ct_safety(patient_id)
    mri_eval = safety_svc.evaluate_mri_safety(patient_id)
    implants = MRIImplantRegistry.query.filter_by(
        patient_id=patient_id, is_active=True).all()
    contrast_history = ContrastAdministration.query.filter_by(
        patient_id=patient_id).order_by(
        ContrastAdministration.administration_time.desc()).limit(10).all()
    dose_svc = RadiationDoseService()
    annual = dose_svc.get_patient_annual_summary(patient_id)
    return render_template('radiology/safety_profile.html',
                           title='Imaging Safety Profile',
                           patient=patient, profile=profile,
                           ct_eval=ct_eval, mri_eval=mri_eval,
                           implants=implants,
                           contrast_history=contrast_history,
                           annual=annual)


@radiology_bp.route('/safety-profile/<int:patient_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('Radiologist', 'Doctor', 'Admin', 'SuperAdmin')
def edit_safety_profile(patient_id):
    """Edit patient imaging safety profile."""
    from app.services.radiology.safety_service import RadiologySafetyService
    patient = db.session.get(Patient, patient_id) or abort(404)
    require_patient_access(patient)
    safety_svc = RadiologySafetyService()
    profile = safety_svc.get_or_create_safety_profile(patient_id)
    if request.method == 'POST':
        profile.pregnancy_status = request.form.get('pregnancy_status', profile.pregnancy_status)
        profile.last_creatinine = request.form.get('last_creatinine', type=float)
        profile.last_egfr = request.form.get('last_egfr', type=float)
        rfd_str = request.form.get('renal_function_date')
        profile.renal_function_date = date.fromisoformat(rfd_str) if rfd_str else None
        profile.previous_contrast_reaction = 'previous_contrast_reaction' in request.form
        profile.contrast_reaction_details = request.form.get('contrast_reaction_details')
        profile.previous_contrast_type = request.form.get('previous_contrast_type')
        profile.mri_screening_status = request.form.get('mri_screening_status', profile.mri_screening_status)
        profile.ct_contraindications = request.form.get('ct_contraindications')
        profile.ct_precautions = request.form.get('ct_precautions')
        profile.special_preparation_notes = request.form.get('special_preparation_notes')
        profile.updated_at = utcnow()
        log_activity('EDIT_IMAGING_SAFETY_PROFILE', 'patient', patient_id)
        db.session.commit()
        flash('Imaging safety profile updated.', 'success')
        return redirect(url_for('radiology.safety_profile', patient_id=patient_id))
    return render_template('radiology/edit_safety_profile.html',
                           title='Edit Safety Profile',
                           patient=patient, profile=profile)


@radiology_bp.route('/safety-profile/<int:patient_id>/implant/add', methods=['POST'])
@login_required
@roles_required('Radiologist', 'Doctor', 'Admin', 'SuperAdmin')
def add_implant(patient_id):
    """Add an MRI implant record."""
    from app.services.radiology.safety_service import RadiologySafetyService
    patient = db.session.get(Patient, patient_id) or abort(404)
    require_patient_access(patient)
    safety_svc = RadiologySafetyService()
    profile = safety_svc.get_or_create_safety_profile(patient_id)
    implant = MRIImplantRegistry(
        patient_id=patient_id,
        profile_id=profile.id,
        device_name=request.form.get('device_name', ''),
        device_category=request.form.get('device_category'),
        manufacturer=request.form.get('manufacturer'),
        model_number=request.form.get('model_number'),
        mr_safety_class=request.form.get('mr_safety_class', 'Unknown'),
        verification_status=request.form.get('verification_status', 'Unverified'),
        verification_source=request.form.get('verification_source'),
        notes=request.form.get('notes'),
    )
    db.session.add(implant)
    log_activity('ADD_MRI_IMPLANT', 'patient', patient_id,
                 f'{implant.device_name} ({implant.mr_safety_class})')
    db.session.commit()
    flash(f'Implant "{implant.device_name}" added.', 'success')
    return redirect(url_for('radiology.safety_profile', patient_id=patient_id))


@radiology_bp.route('/implant/<int:implant_id>/verify', methods=['POST'])
@login_required
@roles_required('Radiologist', 'Admin', 'SuperAdmin')
def verify_implant(implant_id):
    """Verify an MRI implant's safety classification."""
    implant = db.session.get(MRIImplantRegistry, implant_id) or abort(404)
    implant.mr_safety_class = request.form.get('mr_safety_class', implant.mr_safety_class)
    implant.verification_status = 'Verified'
    implant.verification_source = request.form.get('verification_source')
    implant.verification_date = date.today()
    implant.verified_by = current_user.id
    log_activity('VERIFY_MRI_IMPLANT', 'patient', implant.patient_id,
                 f'{implant.device_name} -> {implant.mr_safety_class}')
    db.session.commit()
    flash(f'Implant "{implant.device_name}" verified as {implant.mr_safety_class}.', 'success')
    return redirect(url_for('radiology.safety_profile', patient_id=implant.patient_id))


@radiology_bp.route('/orders/<int:order_id>/screening', methods=['GET', 'POST'])
@login_required
@roles_required('Radiologist', 'RadiologyTechnician', 'Doctor', 'Nurse', 'Admin', 'SuperAdmin')
def safety_screening(order_id):
    """Safety screening for a specific radiology order."""
    from app.services.radiology.safety_service import RadiologySafetyService
    order = _order(order_id)
    require_patient_access(order.patient)
    safety_svc = RadiologySafetyService()
    screenings = ImagingSafetyScreening.query.filter_by(order_id=order_id).all()
    ct_eval = safety_svc.evaluate_ct_safety(order.patient_id, order_id)
    mri_eval = safety_svc.evaluate_mri_safety(order.patient_id, order_id)

    if request.method == 'POST':
        screening_type = request.form.get('screening_type', 'General')
        screening = safety_svc.create_safety_screening(
            order_id=order_id,
            patient_id=order.patient_id,
            screening_type=screening_type,
            screening_status='Cleared',
            pacemaker_screened='pacemaker_screened' in request.form,
            implant_screened='implant_screened' in request.form,
            metallic_foreign_body_screened='metallic_screened' in request.form,
            claustrophobia_screened='claustrophobia_screened' in request.form,
            sedation_required='sedation_required' in request.form,
            previous_contrast_reaction_confirmed='contrast_reaction_confirmed' in request.form,
            renal_function_confirmed='renal_confirmed' in request.form,
            pregnancy_confirmed='pregnancy_confirmed' in request.form,
            allergy_status_confirmed='allergy_confirmed' in request.form,
            screening_completed_by=current_user.id,
            screening_completed_at=utcnow(),
            screening_notes=request.form.get('screening_notes'),
        )
        # Update study record
        from app.models import ImagingStudyRecord
        study_rec = ImagingStudyRecord.query.filter_by(order_id=order_id).first()
        if study_rec:
            study_rec.safety_screening_completed = True
            study_rec.safety_screening_status = 'Cleared'
        log_activity('SAFETY_SCREENING', 'radiology_order', order_id,
                     f'Screening: {screening_type}')
        db.session.commit()
        flash('Safety screening completed.', 'success')
        return redirect(url_for('radiology.safety_screening', order_id=order_id))

    return render_template('radiology/safety_screening.html',
                           title='Safety Screening',
                           order=order, screenings=screenings,
                           ct_eval=ct_eval, mri_eval=mri_eval)


@radiology_bp.route('/orders/<int:order_id>/dose-record', methods=['POST'])
@login_required
@roles_required(*RAD_STAFF)
def record_dose(order_id):
    """Record radiation dose for a completed study."""
    from app.services.radiology.dose_service import RadiationDoseService
    order = _order(order_id)
    dose_svc = RadiationDoseService()
    record = dose_svc.record_dose(
        patient_id=order.patient_id,
        order_id=order_id,
        study_date=order.performed_at or utcnow(),
        accession_number=f'RAD-{order.id:05d}',
        modality=order.imaging_type.name if order.imaging_type else 'Unknown',
        body_region=request.form.get('body_region'),
        study_description=request.form.get('study_description'),
        dose_metric_type=request.form.get('dose_metric_type'),
        dose_value=request.form.get('dose_value', type=float),
        dose_unit=request.form.get('dose_unit'),
        ctdi_vol=request.form.get('ctdi_vol', type=float),
        dlp=request.form.get('dlp', type=float),
        dap=request.form.get('dap', type=float),
        fluoroscopy_time_min=request.form.get('fluoroscopy_time', type=float),
        administered_activity=request.form.get('administered_activity', type=float),
        effective_dose_est=request.form.get('effective_dose_est', type=float),
        is_estimated='is_estimated' in request.form,
        estimation_method=request.form.get('estimation_method'),
        equipment=request.form.get('equipment'),
        ordering_physician_id=order.doctor_id,
    )
    log_activity('RECORD_DOSE', 'radiology_order', order_id,
                 f'Dose recorded: {record.dose_value} {record.dose_unit}')
    db.session.commit()
    flash('Dose information recorded.', 'success')
    return redirect(url_for('radiology.enter_report', order_id=order_id))


@radiology_bp.route('/critical-findings')
@login_required
@roles_required('Radiologist', 'Doctor', 'Admin', 'SuperAdmin')
def critical_findings():
    """View and manage critical radiology findings."""
    q = CriticalFindingNotification.query
    if current_user.has_role('Doctor') and not current_user.has_any_role(
            'Radiologist', 'Admin', 'SuperAdmin'):
        pids = accessible_patient_ids(current_user)
        q = (q.join(RadiologyOrder, CriticalFindingNotification.order_id == RadiologyOrder.id)
              .filter(RadiologyOrder.patient_id.in_(sorted(pids) if pids else [-1])))
    findings = q.order_by(CriticalFindingNotification.created_at.desc()).limit(50).all()
    return render_template('radiology/critical_findings.html',
                           title='Critical Findings',
                           findings=findings)


@radiology_bp.route('/critical-findings/<int:finding_id>/acknowledge', methods=['POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def acknowledge_finding(finding_id):
    """Acknowledge receipt of a critical finding."""
    finding = db.session.get(CriticalFindingNotification, finding_id) or abort(404)
    require_patient_access(finding.order.patient if finding.order else None)
    finding.acknowledged = True
    finding.escalation_status = 'Resolved' if finding.escalation_status == 'Escalated' else finding.escalation_status
    record_event(finding.order.patient_id, 'RADIOLOGY', 'Critical finding acknowledged',
                 finding.finding[:140] if finding.finding else None,
                 source_type='radiology_order', source_id=finding.order_id,
                 department='Radiology')
    finding.acknowledged_by = current_user.id
    finding.acknowledged_at = utcnow()
    log_activity('ACKNOWLEDGE_CRITICAL_FINDING', 'radiology_order', finding.order_id)
    db.session.commit()
    flash('Critical finding acknowledged.', 'success')
    return redirect(url_for('radiology.critical_findings'))


@radiology_bp.route('/protocols')
@login_required
@roles_required(*RAD_STAFF)
def protocols():
    """View imaging preparation protocols."""
    from app.models import ImagingPreparationProtocol
    all_protocols = ImagingPreparationProtocol.query.filter_by(
        is_active=True).order_by(ImagingPreparationProtocol.protocol_name).all()
    return render_template('radiology/protocols.html',
                           title='Imaging Protocols',
                           protocols=all_protocols)


@radiology_bp.route('/reference-levels', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'SuperAdmin')
def reference_levels():
    """Manage institutional reference dose levels."""
    from app.models import ImagingReferenceLevel
    levels = ImagingReferenceLevel.query.filter_by(
        is_active=True).order_by(ImagingReferenceLevel.modality).all()
    if request.method == 'POST':
        level = ImagingReferenceLevel(
            name=request.form.get('name', ''),
            description=request.form.get('description'),
            modality=request.form.get('modality', ''),
            body_region=request.form.get('body_region'),
            effective_dose_threshold_msv=request.form.get('effective_dose_threshold', type=float),
            dlp_threshold_mgycm=request.form.get('dlp_threshold', type=float),
            ctdi_threshold_mgy=request.form.get('ctdi_threshold', type=float),
            age_group=request.form.get('age_group', 'All'),
            is_pregnancy_specific='is_pregnancy_specific' in request.form,
            created_by=current_user.id,
        )
        db.session.add(level)
        log_activity('ADD_REFERENCE_LEVEL', 'system', level.id,
                     f'{level.name} ({level.modality})')
        db.session.commit()
        flash('Reference level added.', 'success')
        return redirect(url_for('radiology.reference_levels'))
    return render_template('radiology/reference_levels.html',
                           title='Reference Dose Levels',
                           levels=levels)


# Import missing models at module level for the routes above
from app.models import (ImagingDoseRecord, MRIImplantRegistry,
                        ContrastAdministration, ImagingSafetyScreening,
                        CriticalFindingNotification, ImagingStudyRecord,
                        PatientImagingSafetyProfile)
from datetime import date
