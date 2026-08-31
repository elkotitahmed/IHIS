from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from app import db
from app.models import (
    LabOrder, LabResult, LabTestCatalog, Patient, Doctor,
)
from app.routes.decorators import roles_required, permissions_required, log_activity, log_change
from app.utils import utcnow, is_clinical_locked, apply_lab_abnormality
from app.services.status import assert_transition, StatusTransitionError
from app.services.notifications import notify, notify_doctor, notify_patient, notify_role
from app.services import tasks as task_svc

lab_bp = Blueprint('lab', __name__)

ALLOWED = ['doctor', 'admin', 'lab_technician', 'SuperAdmin']


def _order(oid):
    return db.session.get(LabOrder, oid) or abort(404)


def _result_of(order):
    return LabResult.query.filter_by(order_id=order.id).first()


def _status_badge(s):
    return {
        'Pending': 'warning', 'Accepted': 'primary', 'Collected': 'info',
        'ReceivedAtLab': 'secondary', 'Processing': 'secondary',
        'Resulted': 'success', 'Verified': 'success', 'Finalized': 'success',
        'Rejected': 'danger', 'Reordered': 'warning', 'Cancelled': 'danger',
    }.get(s, 'secondary')


@lab_bp.route('/dashboard')
@login_required
@roles_required('LabTechnician', 'Doctor', 'Admin', 'SuperAdmin')
def dashboard():
    pending = LabOrder.query.filter(LabOrder.status.in_(
        ('Pending', 'Accepted', 'Collected', 'ReceivedAtLab', 'Processing'))).count()
    collected = LabOrder.query.filter_by(status='Collected').count()
    rejected = LabOrder.query.filter_by(status='Rejected').count()
    completed = LabOrder.query.filter(LabOrder.status.in_(
        ('Resulted', 'Verified', 'Finalized'))).count()
    critical = LabResult.query.filter_by(is_critical=True).count()
    abnormal = LabResult.query.filter_by(is_abnormal=True).count()
    recent_orders = LabOrder.query.order_by(LabOrder.order_date.desc()).limit(10).all()
    # tasks for the lab queue
    lab_tasks = task_svc.department_queue('Laboratory', ('NEW', 'ASSIGNED', 'IN_PROGRESS', 'ON_HOLD'))
    return render_template('lab/dashboard.html', title='Laboratory Dashboard',
                           pending=pending, collected=collected, completed=completed,
                           rejected=rejected, critical=critical, abnormal=abnormal,
                           recent_orders=recent_orders, lab_tasks=lab_tasks,
                           status_badge=_status_badge)


@lab_bp.route('/orders')
@login_required
@roles_required('LabTechnician', 'Doctor', 'Admin', 'SuperAdmin')
def orders():
    all_orders = LabOrder.query.order_by(LabOrder.order_date.desc()).all()
    return render_template('lab/orders.html', title='Lab Orders', orders=all_orders,
                           status_badge=_status_badge)


@lab_bp.route('/order/new', methods=['GET', 'POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def new_order():
    if request.method == 'POST':
        try:
            patient_id = int(request.form.get('patient_id'))
            test_id = int(request.form.get('test_id'))
        except (TypeError, ValueError):
            flash('Please select a valid patient and test.', 'danger')
            return redirect(url_for('lab.new_order'))
        if not Patient.query.get(patient_id) or not LabTestCatalog.query.get(test_id):
            flash('Please select a valid patient and test.', 'danger')
            return redirect(url_for('lab.new_order'))
        priority = request.form.get('priority', 'Normal')
        specimen_type = (request.form.get('specimen_type') or 'Blood').strip() or 'Blood'
        notes = request.form.get('notes')
        order = LabOrder(
            patient_id=patient_id,
            doctor_id=current_user.doctor_profile.id if current_user.doctor_profile else None,
            test_id=test_id,
            priority=priority,
            specimen_type=specimen_type,
            notes=notes,
        )
        db.session.add(order)
        db.session.flush()
        order.accession_number = f'LAB-{order.id:05d}'
        order.barcode = f'{order.id:08d}'
        log_activity('CREATE_LAB_ORDER', 'lab_order', order.id, f'patient={patient_id} test={test_id}')
        db.session.commit()
        # Fan the new order out to lab staff via the task queue + notifications.
        task_svc.create_task(
            title=f'Process lab order #{order.id}: {order.test.test_name if order.test else ""}',
            description=f'Collect and process {specimen_type} sample; enter and verify the result.',
            task_type='LAB', department='Laboratory',
            patient_id=patient_id, assigned_role='LabTechnician',
            priority=priority, related_resource_type='lab_order',
            related_resource_id=order.id)
        db.session.commit()
        notify_role('LabTechnician',
                    f'New lab order #{order.id}',
                    f'A new lab order ({order.test.test_name if order.test else ""}) has been created for patient #{order.patient_id}.',
                    entity_type='lab_order', entity_id=order.id)
        db.session.commit()
        flash('Lab order created; sample workflow started.', 'success')
        return redirect(url_for('lab.orders'))

    patients = Patient.query.all()
    tests = LabTestCatalog.query.filter_by(is_active=True).all()
    return render_template('lab/new_order.html', title='New Lab Order',
                           patients=patients, tests=tests)


@lab_bp.route('/orders/<int:order_id>/accept', methods=['POST'])
@login_required
@roles_required('LabTechnician', 'Admin', 'SuperAdmin')
def accept_order(order_id):
    order = _order(order_id)
    try:
        assert_transition('lab_order', order, 'Accepted')
    except StatusTransitionError as e:
        flash(str(e), 'danger'); db.session.rollback()
        return redirect(url_for('lab.orders'))
    order.status = 'Accepted'
    log_activity('ACCEPT_LAB_ORDER', 'lab_order', order.id)
    db.session.commit()
    flash('Lab order accepted.', 'success')
    return redirect(url_for('lab.orders'))


@lab_bp.route('/orders/<int:order_id>/collect', methods=['POST'])
@login_required
@roles_required('LabTechnician', 'Admin', 'SuperAdmin')
def collect_sample(order_id):
    order = _order(order_id)
    try:
        assert_transition('lab_order', order, 'Collected')
    except StatusTransitionError as e:
        flash(str(e), 'danger'); db.session.rollback()
        return redirect(url_for('lab.orders'))
    order.status = 'Collected'
    order.collected_by = current_user.id
    order.specimen_status = 'Collected'
    order.collection_time = utcnow()
    order.sample_collected_at = utcnow()
    log_activity('COLLECT_SAMPLE', 'lab_order', order.id, f'by uid={current_user.id}')
    db.session.commit()
    flash('Sample marked as collected.', 'success')
    return redirect(url_for('lab.orders'))


@lab_bp.route('/orders/<int:order_id>/receive', methods=['POST'])
@login_required
@roles_required('LabTechnician', 'Admin', 'SuperAdmin')
def receive_sample(order_id):
    order = _order(order_id)
    try:
        assert_transition('lab_order', order, 'ReceivedAtLab')
    except StatusTransitionError as e:
        flash(str(e), 'danger'); db.session.rollback()
        return redirect(url_for('lab.orders'))
    order.status = 'ReceivedAtLab'
    order.specimen_status = 'ReceivedAtLab'
    order.received_at_lab = utcnow()
    log_activity('RECEIVE_LAB_SAMPLE', 'lab_order', order.id)
    db.session.commit()
    flash('Sample received at lab.', 'success')
    return redirect(url_for('lab.orders'))


@lab_bp.route('/orders/<int:order_id>/reject', methods=['POST'])
@login_required
@roles_required('LabTechnician', 'Admin', 'SuperAdmin')
def reject_sample(order_id):
    order = _order(order_id)
    try:
        assert_transition('lab_order', order, 'Rejected')
    except StatusTransitionError as e:
        flash(str(e), 'danger'); db.session.rollback()
        return redirect(url_for('lab.orders'))
    reason = (request.form.get('reason') or 'Not specified').strip()
    order.status = 'Rejected'
    order.specimen_status = 'Rejected'
    order.rejection_reason = reason
    log_activity('REJECT_LAB_ORDER', 'lab_order', order.id, reason)
    db.session.commit()
    # Notify the ordering doctor so they can re-order / address the problem.
    if order.doctor:
        notify_doctor(order.doctor, f'Lab order #{order.id} rejected',
                      f'The sample for "{order.test.test_name if order.test else ''}" was rejected: {reason}',
                      entity_type='lab_order', entity_id=order.id)
        db.session.commit()
    flash('Lab order rejected; doctor notified.', 'success')
    return redirect(url_for('lab.orders'))


@lab_bp.route('/orders/<int:order_id>/reorder', methods=['POST'])
@login_required
@roles_required('LabTechnician', 'Doctor', 'Admin', 'SuperAdmin')
def reorder(order_id):
    order = _order(order_id)
    try:
        assert_transition('lab_order', order, 'Reordered')
    except StatusTransitionError as e:
        flash(str(e), 'danger'); db.session.rollback()
        return redirect(url_for('lab.orders'))
    order.status = 'Reordered'
    log_activity('REORDER_LAB', 'lab_order', order.id)
    db.session.commit()
    flash('Lab order reordered.', 'success')
    return redirect(url_for('lab.orders'))


@lab_bp.route('/orders/<int:order_id>/start', methods=['POST'])
@login_required
@roles_required('LabTechnician', 'Admin', 'SuperAdmin')
def start_processing(order_id):
    order = _order(order_id)
    try:
        assert_transition('lab_order', order, 'Processing')
    except StatusTransitionError as e:
        flash(str(e), 'danger'); db.session.rollback()
        return redirect(url_for('lab.orders'))
    order.status = 'Processing'
    order.specimen_status = 'Processing'
    log_activity('START_LAB_PROCESSING', 'lab_order', order.id)
    db.session.commit()
    flash('Processing started.', 'success')
    return redirect(url_for('lab.orders'))


@lab_bp.route('/orders/<int:order_id>/result', methods=['GET', 'POST'])
@login_required
@roles_required('LabTechnician', 'Admin', 'SuperAdmin')
@permissions_required('LAB_RESULT_CREATE')
def enter_result(order_id):
    order = _order(order_id)
    result = _result_of(order)

    # A verified/locked result cannot be silently overwritten. Corrections must
    # go through the explicit, audited amendment flow.
    if request.method == 'POST' and result and is_clinical_locked(result) and not request.form.get('amend'):
        flash('This result is verified and locked. Use "Request Correction" to amend it with a reason.', 'warning')
        return redirect(url_for('lab.enter_result', order_id=order.id))

    if request.method == 'POST':
        result_value = str(request.form.get('result_value') or '').strip()
        result_notes = request.form.get('result_notes')
        result_unit = request.form.get('result_unit') or (order.test.unit if order.test else '')
        qualitative = request.form.get('qualitative') or None
        manual_abnormal = True if request.form.get('is_abnormal') else False
        manual_critical = True if request.form.get('is_critical') else False
        reason = request.form.get('reason')

        if result and result.status in ('Verified', 'Locked'):
            old_state = {
                'result_value': result.result_value,
                'result_notes': result.result_notes,
                'is_abnormal': result.is_abnormal,
                'is_critical': result.is_critical,
                'status': result.status,
            }
            result.result_value = result_value
            result.result_notes = result_notes
            result.result_unit = result_unit
            result.qualitative = qualitative
            apply_lab_abnormality(result, order)
            if manual_critical:
                result.is_critical = True
            if not result.is_abnormal and manual_abnormal:
                result.is_abnormal = True
            result.validated_by = current_user.id
            result.result_date = utcnow()
            result.status = 'Draft'
            new_state = {
                'result_value': result.result_value,
                'result_notes': result.result_notes,
                'is_abnormal': result.is_abnormal,
                'is_critical': result.is_critical,
                'status': result.status,
            }
            log_change('AMEND_LAB_RESULT', 'lab_result', result.id,
                       old_value=old_state, new_value=new_state,
                       reason=reason or 'No reason provided',
                       details=f'order={order.id}')
            flash('Correction recorded and sent for re-verification.', 'success')
        elif result:
            result.result_value = result_value
            result.result_notes = result_notes
            result.result_unit = result_unit
            result.qualitative = qualitative
            apply_lab_abnormality(result, order)
            if manual_critical:
                result.is_critical = True
            if not result.is_abnormal and manual_abnormal:
                result.is_abnormal = True
            result.validated_by = current_user.id
            result.result_date = utcnow()
        else:
            result = LabResult(
                order_id=order.id, result_value=result_value,
                result_notes=result_notes, result_unit=result_unit,
                qualitative=qualitative,
                validated_by=current_user.id, created_by=current_user.id)
            apply_lab_abnormality(result, order)
            if manual_critical:
                result.is_critical = True
            if not result.is_abnormal and manual_abnormal:
                result.is_abnormal = True
            db.session.add(result)

        try:
            assert_transition('lab_order', order, 'Resulted')
            order.status = 'Resulted'
        except StatusTransitionError:
            # If order is already past Resulted (e.g. re-editing before verify),
            # simply leave it where it is.
            if order.status not in ('Resulted', 'Verified', 'Finalized'):
                order.status = 'Resulted'
        log_activity('ENTER_LAB_RESULT', 'lab_order', order.id)
        db.session.commit()
        # If a critical panic value was flagged, escalate immediately.
        if manual_critical or (result and result.is_critical):
            notify_role('Doctor', f'CRITICAL lab result — order #{order.id}',
                        f'Critical value: {result_value} ({order.test.test_name if order.test else ""}). Review immediately.',
                        notification_type='critical', entity_type='lab_order', entity_id=order.id)
            db.session.commit()
        return redirect(url_for('lab.orders'))

    return render_template('lab/result.html', title='Enter Result', order=order,
                           result=result, status_badge=_status_badge)


@lab_bp.route('/orders/<int:order_id>/verify', methods=['POST'])
@login_required
@roles_required('LabTechnician', 'Admin', 'SuperAdmin')
@permissions_required('LAB_RESULT_VERIFY')
def verify_result(order_id):
    order = _order(order_id)
    result = _result_of(order)
    if not result:
        flash('No result to verify. Enter the result first.', 'warning')
        return redirect(url_for('lab.enter_result', order_id=order.id))
    if is_clinical_locked(result):
        flash('Result is already verified/locked.', 'info')
        return redirect(url_for('lab.enter_result', order_id=order.id))
    try:
        assert_transition('lab_order', order, 'Verified')
        order.status = 'Verified'
    except StatusTransitionError as e:
        if order.status not in ('Verified', 'Finalized'):
            order.status = 'Verified'
    result.status = 'Verified'
    result.validated_by = current_user.id
    log_activity('VERIFY_LAB_RESULT', 'lab_result', result.id,
                 f'Verified by {current_user.full_name}')
    if order.test and order.test.price:
        from app.services.billing import ensure_bill_for_lab
        ensure_bill_for_lab(order.id)
    notify_patient(order.patient, 'Lab result ready',
                   f'Your lab result "{order.test.test_name if order.test else ''}" has been verified and is available.',
                   entity_type='lab_order', entity_id=order.id)
    # Notify the ordering doctor that their result is ready.
    if order.doctor:
        notify_doctor(order.doctor, f'Lab result ready — order #{order.id}',
                      f'The result for "{order.test.test_name if order.test else ''}" has been verified.',
                      entity_type='lab_order', entity_id=order.id)
    db.session.commit()
    flash('Lab result verified and locked.', 'success')
    return redirect(url_for('lab.enter_result', order_id=order.id))


@lab_bp.route('/orders/<int:order_id>/cancel', methods=['POST'])
@login_required
@roles_required('LabTechnician', 'Doctor', 'Admin', 'SuperAdmin')
def cancel_order(order_id):
    order = _order(order_id)
    try:
        assert_transition('lab_order', order, 'Cancelled')
    except StatusTransitionError as e:
        flash(str(e), 'danger'); db.session.rollback()
        return redirect(url_for('lab.orders'))
    order.status = 'Cancelled'
    log_activity('CANCEL_LAB_ORDER', 'lab_order', order.id)
    if order.doctor:
        notify_doctor(order.doctor, f'Lab order #{order.id} cancelled',
                      f'The lab order "{order.test.test_name if order.test else ''}" was cancelled.',
                      entity_type='lab_order', entity_id=order.id)
    db.session.commit()
    flash('Lab order cancelled.', 'success')
    return redirect(url_for('lab.orders'))


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


@lab_bp.route('/result/<int:result_id>/finalize', methods=['POST'])
@login_required
@roles_required('LabTechnician', 'Admin', 'SuperAdmin')
def finalize_result(result_id):
    result = db.session.get(LabResult, result_id) or abort(404)
    if result.status != 'Verified':
        flash('Only verified results can be finalized.', 'warning')
        return redirect(url_for('lab.orders', _anchor=f'order-{result.order_id}'))
    result.status = 'Finalized'
    order = result.order
    if order and order.status == 'Verified':
        order.status = 'Finalized'
    log_activity('FINALIZE_LAB_RESULT', 'lab_result', result.id)
    db.session.commit()
    flash('Lab result finalized.', 'success')
    return redirect(url_for('lab.orders'))
