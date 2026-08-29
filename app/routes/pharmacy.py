from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime, date
from app import db
from app.models import (
    PharmacyInventory, Medication, Prescription, PrescriptionItem,
    DispensingRecord, Patient, DrugInteraction,
)
from app.routes.decorators import roles_required, log_activity

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
        log_activity('ADD_INVENTORY', 'pharmacy_inventory', None,
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
def dispense(id):
    rx = Prescription.query.get_or_404(id)
    item_id = request.form.get('item_id')
    item = None
    if item_id:
        item = PrescriptionItem.query.filter_by(id=int(item_id),
                                                prescription_id=rx.id).first()
    if item is None:
        items = [i for i in rx.items if i.status != 'Dispensed']
        if not items:
            flash('All items already dispensed.', 'warning')
            return redirect(url_for('pharmacy.prescriptions'))
        item = items[0]

    if item.status == 'Dispensed':
        flash('This item was already dispensed.', 'warning')
        return redirect(url_for('pharmacy.prescriptions'))

    try:
        quantity = max(1, int(request.form.get('quantity') or item.quantity or 1))
    except ValueError:
        quantity = item.quantity or 1

    # FEFO: dispense from the batch expiring soonest with sufficient stock.
    inv = PharmacyInventory.query.filter(
        PharmacyInventory.medication_id == item.medication_id,
        PharmacyInventory.quantity > 0,
    ).order_by(
        PharmacyInventory.expiry_date.asc().nulls_last()
    ).all()

    available = sum(i.quantity for i in inv)
    med_name = item.medication.generic_name if item.medication else 'medication'
    if available < quantity:
        flash(f'Insufficient stock for {med_name}. Available: {available}, '
              f'requested: {quantity}.', 'danger')
        return redirect(url_for('pharmacy.prescriptions'))

    remaining = quantity
    for stock in inv:
        if remaining <= 0:
            break
        take = min(stock.quantity, remaining)
        stock.quantity -= take
        remaining -= take

    item.status = 'Dispensed'
    record = DispensingRecord(
        prescription_id=rx.id,
        item_id=item.id,
        pharmacist_id=current_user.id,
        quantity=quantity,
    )
    db.session.add(record)
    log_activity('DISPENSE_PRESCRIPTION', 'prescription', rx.id,
                 f'medication_id={item.medication_id} qty={quantity}')
    if rx.dispensed():
        rx.status = 'Dispensed'
    db.session.commit()
    flash(f'Dispensed "{med_name}" ({quantity}).', 'success')
    return redirect(url_for('pharmacy.prescriptions'))


@pharmacy_bp.route('/medications', methods=['GET', 'POST'])
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
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
