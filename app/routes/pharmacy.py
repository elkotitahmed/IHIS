from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime, date
from app import db
from app.models import (
    PharmacyInventory, Medication, Prescription, PrescriptionItem,
    DispensingRecord, Patient, DrugInteraction, StockTransaction,
)
from app.routes.decorators import roles_required, permissions_required, log_activity, log_change
from app.services.status import assert_transition, StatusTransitionError
from app.services.notifications import notify_doctor, notify_patient, notify_role

pharmacy_bp = Blueprint('pharmacy', __name__)


@pharmacy_bp.route('/dashboard')
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
def dashboard():
    total_medications = Medication.query.filter_by(is_active=True).count()
    low_stock = PharmacyInventory.query.filter(
        PharmacyInventory.quantity <= PharmacyInventory.reorder_level
    ).all()
    low_stock_count = len(low_stock)
    recent_dispensed = DispensingRecord.query.order_by(
        DispensingRecord.dispensed_at.desc()
    ).limit(10).all()
    return render_template(
        'pharmacy/dashboard.html',
        total_medications=total_medications,
        low_stock_count=low_stock_count,
        recent_dispensed_count=DispensingRecord.query.count(),
        low_stock=low_stock,
        recent_dispensed=recent_dispensed,
    )


@pharmacy_bp.route('/inventory')
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
def inventory():
    items = PharmacyInventory.query.join(Medication).order_by(
        PharmacyInventory.updated_at.desc()
    ).all()
    return render_template('pharmacy/inventory.html', items=items, today=date.today())


@pharmacy_bp.route('/inventory/add', methods=['GET', 'POST'])
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('INVENTORY_MANAGE')
def add_inventory():
    if request.method == 'POST':
        item = PharmacyInventory(
            medication_id=int(request.form['medication_id']),
            quantity=int(request.form.get('quantity', 0)),
            reorder_level=int(request.form.get('reorder_level', 10)),
            unit_cost=float(request.form.get('unit_cost', 0)),
            selling_price=float(request.form.get('selling_price', 0)),
            expiry_date=datetime.strptime(
                request.form['expiry_date'], '%Y-%m-%d'
            ).date() if request.form.get('expiry_date') else None,
            batch_number=request.form.get('batch_number', ''),
        )
        db.session.add(item)
        db.session.flush()
        db.session.add(StockTransaction(
            inventory_id=item.id,
            medication_id=item.medication_id,
            tx_type='RECEIVE',
            quantity_change=item.quantity,
            quantity_after=item.quantity,
            unit_cost=item.unit_cost,
            reference=item.batch_number or None,
            notes='Initial stock received',
            user_id=current_user.id,
        ))
        log_activity('ADD_INVENTORY', 'pharmacy_inventory', item.id,
                      f'medication_id={item.medication_id} qty={item.quantity}')
        db.session.commit()
        flash('Inventory item added successfully.', 'success')
        return redirect(url_for('pharmacy.inventory'))

    medications = Medication.query.filter_by(is_active=True).all()
    return render_template('pharmacy/add_inventory.html', medications=medications)


@pharmacy_bp.route('/prescriptions')
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
def prescriptions():
    pending = Prescription.query.filter(
        Prescription.status != 'Dispensed'
    ).order_by(Prescription.prescribed_date.desc()).all()
    return render_template('pharmacy/prescriptions.html', prescriptions=pending)


@pharmacy_bp.route('/prescriptions/<int:id>/dispense', methods=['POST'])
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('PRESCRIPTION_DISPENSE')
def dispense(id):
    rx = Prescription.query.get_or_404(id)
    if rx.status == 'Cancelled':
        flash('Cannot dispense: this prescription has been cancelled.', 'warning')
        return redirect(url_for('pharmacy.prescriptions'))
    item_id = request.form.get('item_id')
    item = None
    if item_id:
        item = PrescriptionItem.query.filter_by(id=int(item_id),
                                                prescription_id=rx.id).first()
    if item is None:
        items = [i for i in rx.items if i.status != 'Cancelled' and i.remaining_qty() > 0]
        if not items:
            flash('All items already fully dispensed.', 'warning')
            return redirect(url_for('pharmacy.prescriptions'))
        item = items[0]

    if item.status == 'Cancelled':
        flash('This item has been cancelled.', 'warning')
        return redirect(url_for('pharmacy.prescriptions'))

    raw_qty = request.form.get('quantity')
    try:
        requested = int(raw_qty) if raw_qty not in (None, '') else 0
    except ValueError:
        requested = 0
    # Cap at the quantity still owed so we never over-dispense.
    remaining_qty = item.remaining_qty()
    quantity = remaining_qty if requested <= 0 else min(requested, remaining_qty)
    if quantity <= 0:
        flash('This item is already fully dispensed.', 'warning')
        return redirect(url_for('pharmacy.prescriptions'))

    # FEFO: dispense from the batch expiring soonest with sufficient stock.
    inv = PharmacyInventory.query.filter(
        PharmacyInventory.medication_id == item.medication_id,
        PharmacyInventory.quantity > 0,
    ).order_by(
        PharmacyInventory.expiry_date.asc().nulls_last()
    ).all()

    available = sum(i.quantity for i in inv)
    med_name = item.medication.generic_name if item.medication else 'medication'
    if available <= 0:
        flash(f'No stock available for {med_name}.', 'danger')
        return redirect(url_for('pharmacy.prescriptions'))

    # Partial dispense: give what we have now; the remainder stays pending.
    dispensed = min(available, quantity)
    partial = dispensed < quantity

    remaining = dispensed
    before = {s.id: s.quantity for s in inv}
    for stock in inv:
        if remaining <= 0:
            break
        take = min(stock.quantity, remaining)
        stock.quantity -= take
        db.session.add(StockTransaction(
            inventory_id=stock.id,
            medication_id=item.medication_id,
            tx_type='DISPENSE',
            quantity_change=-take,
            quantity_after=stock.quantity,
            reference=f'rx-{rx.id}',
            notes=f'Dispense for prescription #{rx.id}',
            user_id=current_user.id,
        ))
        remaining -= take
    after = {s.id: s.quantity for s in inv}

    if not partial:
        item.status = 'Dispensed'
    # Otherwise item stays Active; it is simply recorded as partially dispensed
    # via the sum of its dispensing records.

    record = DispensingRecord(
        prescription_id=rx.id,
        item_id=item.id,
        pharmacist_id=current_user.id,
        quantity=dispensed,
        notes='Partial dispense' if partial else None,
    )
    db.session.add(record)
    db.session.flush()
    log_activity('DISPENSE_PRESCRIPTION', 'prescription', rx.id,
                 f'medication_id={item.medication_id} qty={dispensed}'
                 + (' partial' if partial else ''))
    # Stock-movement audit: capture before/after so any inventory change is
    # attributable to a pharmacist and a prescription.
    log_change('STOCK_CHANGE', 'pharmacy_inventory', item.medication_id,
               old_value=before, new_value=after,
               reason=f'Dispensed {dispensed} for prescription #{rx.id}'
                      + (' (partial)' if partial else ''),
               details=f'pharmacist={current_user.id}')
    if rx.fully_dispensed():
        rx.status = 'Dispensed'
        from app.services.notifications import notify_patient
        notify_patient(rx.patient, 'Prescription dispensed',
                       f'Your prescription #{rx.id} has been fully dispensed and is ready for pickup.')
    from app.services.billing import ensure_bill_for_pharmacy
    ensure_bill_for_pharmacy(rx.id)
    db.session.commit()
    if partial:
        flash(f'Partially dispensed "{med_name}": {dispensed} of {quantity} '
              f'({dispensed} available). Remaining stock pending.',
              'warning')
    else:
        flash(f'Dispensed "{med_name}" ({dispensed}).', 'success')
    return redirect(url_for('pharmacy.prescriptions'))


@pharmacy_bp.route('/prescriptions/<int:rx_id>/reject', methods=['POST'])
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('PRESCRIPTION_DISPENSE')
def reject(rx_id):
    """Reject an Active prescription with a reason, notifying the doctor."""
    rx = Prescription.query.get_or_404(rx_id)
    if rx.status in ('Dispensed', 'Cancelled'):
        flash('This prescription has already been dispensed or cancelled.', 'warning')
        return redirect(url_for('pharmacy.prescriptions'))
    reason = (request.form.get('reason') or 'Not specified').strip()
    old = rx.status
    rx.status = 'Cancelled'
    for item in rx.items:
        if item.status != 'Dispensed':
            item.status = 'Cancelled'
    log_change('REJECT_PRESCRIPTION', 'prescription', rx.id,
               old_value={'status': old}, new_value={'status': 'Cancelled'},
               reason=reason, details=f'pharmacist={current_user.id}')
    if rx.doctor:
        notify_doctor(rx.doctor, f'Prescription #{rx.id} rejected',
                      f'Your prescription #{rx.id} was rejected by pharmacy: {reason}',
                      notification_type='critical' if 'interaction' in reason.lower() else 'in-app',
                      entity_type='prescription', entity_id=rx.id)
    from app.services.notifications import notify_patient
    notify_patient(rx.patient, 'Prescription rejected',
                   f'Your prescription #{rx.id} could not be dispensed. Please contact your doctor.',
                   entity_type='prescription', entity_id=rx.id)
    db.session.commit()
    flash('Prescription rejected; prescribing doctor notified.', 'warning')
    return redirect(url_for('pharmacy.prescriptions'))


@pharmacy_bp.route('/prescriptions/<int:rx_id>')
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
def prescription_detail(rx_id):
    rx = Prescription.query.get_or_404(rx_id)
    return render_template('pharmacy/prescription_detail.html', rx=rx)


@pharmacy_bp.route('/inventory/<int:inv_id>/adjust', methods=['POST'])
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('INVENTORY_MANAGE')
def adjust_stock(inv_id):
    inv = db.session.get(PharmacyInventory, inv_id)
    if not inv:
        return redirect(url_for('pharmacy.inventory'))
    try:
        delta = int(request.form.get('delta', 0))
    except ValueError:
        delta = 0
    tx_type = request.form.get('tx_type') or (
        'ADJUSTMENT' if delta >= 0 and not request.form.get('reason') else 'ADJUSTMENT')
    if delta == 0:
        flash('Enter a non-zero quantity change.', 'warning')
        return redirect(url_for('pharmacy.inventory', _anchor=f'batch-{inv.id}'))
    new_qty = inv.quantity + delta
    if new_qty < 0:
        new_qty = 0
    actual_delta = new_qty - inv.quantity
    inv.quantity = new_qty
    db.session.add(StockTransaction(
        inventory_id=inv.id, medication_id=inv.medication_id,
        tx_type=request.form.get('tx_type') or 'ADJUSTMENT',
        quantity_change=actual_delta, quantity_after=inv.quantity,
        reference=request.form.get('reference') or None,
        notes=request.form.get('note') or None,
        user_id=current_user.id,
    ))
    log_activity('ADJUST_STOCK', 'pharmacy_inventory', inv.id,
                 f'delta={actual_delta}')
    db.session.commit()
    flash('Stock adjusted.', 'success')
    return redirect(url_for('pharmacy.inventory', _anchor=f'batch-{inv.id}'))


@pharmacy_bp.route('/transactions')
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
def transactions():
    txs = StockTransaction.query.order_by(StockTransaction.created_at.desc()).limit(200).all()
    return render_template('pharmacy/transactions.html', txs=txs)


@pharmacy_bp.route('/medications', methods=['GET', 'POST'])
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('INVENTORY_MANAGE')
def medications():
    if request.method == 'POST':
        med = Medication(
            generic_name=request.form['generic_name'],
            brand_name=request.form.get('brand_name', ''),
            category=request.form.get('category', ''),
            contraindications=request.form.get('contraindications', ''),
            side_effects=request.form.get('side_effects', ''),
        )
        db.session.add(med)
        log_activity('ADD_MEDICATION', 'medication', None,
                      f'generic={med.generic_name}')
        db.session.commit()
        flash('Medication added successfully.', 'success')
        return redirect(url_for('pharmacy.medications'))

    search = request.args.get('q', '').strip()
    query = Medication.query
    if search:
        query = query.filter(
            Medication.generic_name.ilike(f'%{search}%') |
            Medication.brand_name.ilike(f'%{search}%')
        )
    meds = query.order_by(Medication.generic_name).all()
    return render_template('pharmacy/medications.html', medications=meds, search=search)
