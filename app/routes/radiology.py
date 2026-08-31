from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, send_file, current_app
from flask_login import login_required, current_user
import os
from app import db
from app.models import RadiologyOrder, RadiologyReport, ImagingType, Patient, User
from app.routes.decorators import roles_required, permissions_required, log_activity, log_change, save_upload
from app.access import require_patient_access
from app.utils import utcnow, is_clinical_locked
from app.services.status import assert_transition, StatusTransitionError
from app.services.notifications import notify_doctor, notify_patient, notify_role
from app.services import tasks as task_svc

radiology_bp = Blueprint('radiology', __name__)


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
@roles_required('Radiologist', 'Doctor', 'Admin', 'SuperAdmin')
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
@roles_required('Radiologist', 'Doctor', 'Admin', 'SuperAdmin')
def orders():
    all_orders = RadiologyOrder.query.order_by(RadiologyOrder.order_date.desc()).all()
    return render_template('radiology/orders.html', title='Radiology Orders',
                           orders=all_orders, status_badge=_status_badge)


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
        order = RadiologyOrder(
            patient_id=patient_id,
            doctor_id=current_user.doctor_profile.id if current_user.doctor_profile else None,
            imaging_type_id=imaging_type_id,
            priority=request.form.get('priority', 'Normal'),
            notes=request.form.get('notes'),
        )
        db.session.add(order)
        db.session.flush()
        log_activity('CREATE_RADIOLOGY_ORDER', 'radiology_order', order.id,
                     f'patient={patient_id}')
        db.session.commit()
        task_svc.create_task(
            title=f'Perform study #{order.id}: {order.imaging_type.name if order.imaging_type else ""}',
            description='Schedule, capture, and prepare the study for reporting.',
            task_type='RADIOLOGY', department='Radiology',
            patient_id=patient_id, assigned_role='Radiologist',
            priority=order.priority, related_resource_type='radiology_order',
            related_resource_id=order.id)
        db.session.commit()
        notify_role('Radiologist',
                    f'New radiology order #{order.id}',
                    f'A new imaging order ({order.imaging_type.name if order.imaging_type else ""}) has been created for patient #{order.patient_id}.',
                    entity_type='radiology_order', entity_id=order.id)
        db.session.commit()
        flash('Radiology order created; study workflow started.', 'success')
        return redirect(url_for('radiology.orders'))

    patients = Patient.query.all()
    imaging = ImagingType.query.all()
    return render_template('radiology/new_order.html', title='New Radiology Order',
                           patients=patients, imaging=imaging)


@radiology_bp.route('/orders/<int:order_id>/schedule', methods=['POST'])
@login_required
@roles_required('Radiologist', 'Admin', 'SuperAdmin')
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
@roles_required('Radiologist', 'Admin', 'SuperAdmin')
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
@roles_required('Radiologist', 'Admin', 'SuperAdmin')
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
    db.session.commit()
    flash('Study performed; ready for reporting.', 'success')
    return redirect(url_for('radiology.orders'))


@radiology_bp.route('/orders/<int:order_id>/upload', methods=['POST'])
@login_required
@roles_required('Radiologist', 'Admin', 'SuperAdmin')
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

        if report and report.status in ('Signed', 'Locked'):
            old_state = {
                'findings': report.findings,
                'impression': report.impression,
                'recommendation': report.recommendation,
                'status': report.status,
            }
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
        order.status = 'Reported'
    if order.imaging_type and order.imaging_type.price:
        from app.services.billing import ensure_bill_for_radiology
        ensure_bill_for_radiology(order.id)
    notify_patient(order.patient, 'Radiology report ready',
                   f'Your radiology report ({order.imaging_type.name if order.imaging_type else ""}) has been signed and is available.',
                   entity_type='radiology_order', entity_id=order.id)
    if order.doctor:
        notify_doctor(order.doctor, f'Radiology report ready — order #{order.id}',
                      f'The report for "{order.imaging_type.name if order.imaging_type else ""}" has been signed.',
                      entity_type='radiology_order', entity_id=order.id)
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
    if order.doctor:
        notify_doctor(order.doctor, f'Radiology order #{order.id} cancelled',
                      f'The study "{order.imaging_type.name if order.imaging_type else ""}" was cancelled.',
                      entity_type='radiology_order', entity_id=order.id)
    db.session.commit()
    flash('Radiology order cancelled.', 'success')
    return redirect(url_for('radiology.orders'))
