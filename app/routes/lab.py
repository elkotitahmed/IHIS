from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime
from app import db
from app.models import (
    LabOrder, LabResult, LabTestCatalog, Patient, Doctor,
)
from app.routes.decorators import roles_required, log_activity

lab_bp = Blueprint('lab', __name__)

ALLOWED = ['doctor', 'admin', 'lab_technician', 'SuperAdmin']


@lab_bp.route('/dashboard')
@login_required
@roles_required('LabTechnician', 'Doctor', 'Admin', 'SuperAdmin')
def dashboard():
    pending = LabOrder.query.filter_by(status='Pending').count()
    collected = LabOrder.query.filter_by(status='SampleCollected').count()
    completed = LabOrder.query.filter_by(status='Completed').count()
    critical = LabResult.query.filter_by(is_abnormal=True).count()
    recent_orders = LabOrder.query.order_by(LabOrder.order_date.desc()).limit(10).all()
    return render_template('lab/dashboard.html', title='Laboratory Dashboard',
                           pending=pending, collected=collected, completed=completed,
                           critical=critical, recent_orders=recent_orders)


@lab_bp.route('/orders')
@login_required
@roles_required('LabTechnician', 'Doctor', 'Admin', 'SuperAdmin')
def orders():
    all_orders = LabOrder.query.order_by(LabOrder.order_date.desc()).all()
    return render_template('lab/orders.html', title='Lab Orders', orders=all_orders)


@lab_bp.route('/order/new', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def new_order():
    if request.method == 'POST':
        patient_id = request.form.get('patient_id')
        test_id = request.form.get('test_id')
        priority = request.form.get('priority', 'Normal')
        notes = request.form.get('notes')
        order = LabOrder(
            patient_id=patient_id,
            doctor_id=current_user.doctor_profile.id if current_user.doctor_profile else None,
            test_id=test_id,
            priority=priority,
            notes=notes,
        )
        db.session.add(order)
        log_activity('CREATE_LAB_ORDER', 'lab_order', None, f'patient={patient_id}')
        db.session.commit()
        flash('Lab order created successfully.', 'success')
        return redirect(url_for('lab.orders'))

    patients = Patient.query.all()
    tests = LabTestCatalog.query.filter_by(is_active=True).all()
    return render_template('lab/new_order.html', title='New Lab Order',
                           patients=patients, tests=tests)


@lab_bp.route('/orders/<int:order_id>/collect', methods=['POST'])
@login_required
@roles_required('LabTechnician', 'Admin', 'SuperAdmin')
def collect_sample(order_id):
    order = LabOrder.query.get_or_404(order_id)
    order.status = 'SampleCollected'
    order.sample_collected_at = datetime.utcnow()
    log_activity('COLLECT_SAMPLE', 'lab_order', order.id)
    db.session.commit()
    flash('Sample marked as collected.', 'success')
    return redirect(url_for('lab.orders'))


@lab_bp.route('/orders/<int:order_id>/result', methods=['GET', 'POST'])
@login_required
@roles_required('LabTechnician', 'Admin', 'SuperAdmin')
def enter_result(order_id):
    order = LabOrder.query.get_or_404(order_id)

    if request.method == 'POST':
        result_value = request.form.get('result_value')
        result_notes = request.form.get('result_notes')
        is_abnormal = True if request.form.get('is_abnormal') else False

        result = LabResult.query.filter_by(order_id=order.id).first()
        if result:
            result.result_value = result_value
            result.result_notes = result_notes
            result.is_abnormal = is_abnormal
            result.validated_by = current_user.id
            result.result_date = datetime.utcnow()
        else:
            db.session.add(LabResult(
                order_id=order.id, result_value=result_value,
                result_notes=result_notes, is_abnormal=is_abnormal,
                validated_by=current_user.id))

        order.status = 'Completed'
        log_activity('ENTER_LAB_RESULT', 'lab_order', order.id)
        db.session.commit()
        flash('Lab result entered successfully!', 'success')
        return redirect(url_for('lab.orders'))

    return render_template('lab/result.html', title='Enter Result', order=order)


@lab_bp.route('/catalog')
@login_required
@roles_required('LabTechnician', 'Admin', 'SuperAdmin')
def catalog():
    tests = LabTestCatalog.query.order_by(LabTestCatalog.category).all()
    return render_template('lab/catalog.html', title='Test Catalog', tests=tests)


@lab_bp.route('/catalog/add', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'SuperAdmin')
def add_test():
    if request.method == 'POST':
        db.session.add(LabTestCatalog(
            test_name=request.form.get('test_name'),
            category=request.form.get('category'),
            normal_range=request.form.get('normal_range'),
            unit=request.form.get('unit'),
            price=float(request.form.get('price') or 0),
        ))
        db.session.commit()
        flash('Test added to catalog.', 'success')
        return redirect(url_for('lab.catalog'))
    return render_template('lab/add_test.html', title='Add Test')
