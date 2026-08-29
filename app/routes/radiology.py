from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime
from app import db
from app.models import RadiologyOrder, RadiologyReport, ImagingType, Patient
from app.routes.decorators import roles_required, log_activity, save_upload

radiology_bp = Blueprint('radiology', __name__)


@radiology_bp.route('/dashboard')
@login_required
@roles_required('Radiologist', 'Doctor', 'Admin', 'SuperAdmin')
def dashboard():
    pending = RadiologyOrder.query.filter_by(status='Pending').count()
    scheduled = RadiologyOrder.query.filter_by(status='Scheduled').count()
    completed = RadiologyOrder.query.filter_by(status='Completed').count()
    recent = RadiologyOrder.query.order_by(RadiologyOrder.order_date.desc()).limit(10).all()
    return render_template('radiology/dashboard.html', title='Radiology Dashboard',
                           pending=pending, scheduled=scheduled, completed=completed, recent=recent)


@radiology_bp.route('/orders')
@login_required
@roles_required('Radiologist', 'Doctor', 'Admin', 'SuperAdmin')
def orders():
    all_orders = RadiologyOrder.query.order_by(RadiologyOrder.order_date.desc()).all()
    return render_template('radiology/orders.html', title='Radiology Orders', orders=all_orders)


@radiology_bp.route('/order/new', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def new_order():
    if request.method == 'POST':
        order = RadiologyOrder(
            patient_id=request.form.get('patient_id'),
            doctor_id=current_user.doctor_profile.id if current_user.doctor_profile else None,
            imaging_type_id=request.form.get('imaging_type_id'),
            priority=request.form.get('priority', 'Normal'),
            notes=request.form.get('notes'),
        )
        db.session.add(order)
        log_activity('CREATE_RADIOLOGY_ORDER', 'radiology_order', None,
                     f"patient={order.patient_id}")
        db.session.commit()
        flash('Radiology order created.', 'success')
        return redirect(url_for('radiology.orders'))

    patients = Patient.query.all()
    imaging = ImagingType.query.all()
    return render_template('radiology/new_order.html', title='New Radiology Order',
                           patients=patients, imaging=imaging)


@radiology_bp.route('/orders/<int:order_id>/schedule', methods=['POST'])
@login_required
@roles_required('Radiologist', 'Admin', 'SuperAdmin')
def schedule(order_id):
    order = RadiologyOrder.query.get_or_404(order_id)
    order.status = 'Scheduled'
    db.session.commit()
    flash('Study scheduled.', 'success')
    return redirect(url_for('radiology.orders'))


@radiology_bp.route('/orders/<int:order_id>/upload', methods=['POST'])
@login_required
@roles_required('Radiologist', 'Admin', 'SuperAdmin')
def upload_images(order_id):
    order = RadiologyOrder.query.get_or_404(order_id)
    files = request.files.getlist('images')
    urls = []
    for f in files:
        url = save_upload(f, 'radiology_images', {'png', 'jpg', 'jpeg', 'dcm'})
        if url:
            urls.append(url)
    if urls:
        existing = order.image_urls or ''
        order.image_urls = (existing + ',' + ','.join(urls)).strip(',')
        order.status = 'In Progress'
        db.session.commit()
        flash('Images uploaded successfully.', 'success')
    else:
        flash('No valid images uploaded.', 'warning')
    return redirect(url_for('radiology.orders'))


@radiology_bp.route('/orders/<int:order_id>/report', methods=['GET', 'POST'])
@login_required
@roles_required('Radiologist', 'Admin', 'SuperAdmin')
def enter_report(order_id):
    order = RadiologyOrder.query.get_or_404(order_id)

    if request.method == 'POST':
        findings = request.form.get('findings')
        impression = request.form.get('impression')
        recommendation = request.form.get('recommendation')

        report = RadiologyReport.query.filter_by(order_id=order.id).first()
        if report:
            report.findings = findings
            report.impression = impression
            report.recommendation = recommendation
            report.reported_by = current_user.id
            report.report_date = datetime.utcnow()
        else:
            db.session.add(RadiologyReport(
                order_id=order.id, findings=findings, impression=impression,
                recommendation=recommendation, reported_by=current_user.id))

        order.status = 'Completed'
        log_activity('ENTER_RADIOLOGY_REPORT', 'radiology_order', order.id)
        db.session.commit()
        flash('Radiology report submitted!', 'success')
        return redirect(url_for('radiology.orders'))

    return render_template('radiology/report.html', title='Enter Radiology Report', order=order)
