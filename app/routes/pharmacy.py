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
from app.services.timeline import record_event
from app.services import alerts as alert_svc
from app.services.patient_safety import patient_safety_context
from app.utils import utcnow

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
    from datetime import timedelta
    expiring_soon = PharmacyInventory.query.filter(
        PharmacyInventory.expiry_date.isnot(None),
        PharmacyInventory.expiry_date <= date.today() + timedelta(days=60),
        PharmacyInventory.quantity > 0,
    ).order_by(PharmacyInventory.expiry_date.asc()).all()
    expiring_count = len(expiring_soon)
    return render_template('pharmacy/dashboard.html', title='Pharmacy Dashboard',
        total_medications=total_medications,
        low_stock_count=low_stock_count,
        recent_dispensed_count=DispensingRecord.query.count(),
        low_stock=low_stock,
        recent_dispensed=recent_dispensed,
        expiring_soon=expiring_soon,
        expiring_count=expiring_count,
        today=date.today(),
    )


@pharmacy_bp.route('/inventory')
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
def inventory():
    today = date.today()
    f = request.args.get('f', '').strip()
    items = PharmacyInventory.query.join(Medication).order_by(
        PharmacyInventory.updated_at.desc()
    ).all()

    def classify(it):
        expired = it.expiry_date is not None and it.expiry_date < today
        low = it.quantity <= it.reorder_level
        expiring = (not expired) and it.expiry_date and (
            it.expiry_date - today).days <= 90
        if expired:
            return 'expired'
        if low:
            return 'low'
        if expiring:
            return 'expiring'
        return 'ok'

    if f in ('expired', 'low', 'expiring', 'ok'):
        items = [i for i in items if classify(i) == f]
    return render_template('pharmacy/inventory.html', title='Pharmacy Inventory', items=items,
                           today=today, f=f)


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
    return render_template('pharmacy/add_inventory.html', title='Add Inventory', medications=medications)


@pharmacy_bp.route('/prescriptions')
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
def prescriptions():
    pending = Prescription.query.filter(
        Prescription.status.notin_(('Dispensed', 'Cancelled'))
    ).order_by(Prescription.prescribed_date.desc()).all()

    def partial_key(rx):
        # Partially dispensed prescriptions float to the top of the queue so
        # the pharmacist finishes what is owed before starting new work.
        partial = any(i.dispensed_qty() > 0 and i.status != 'Dispensed'
                      for i in list(rx.items))
        return (0 if partial else 1, -(rx.id or 0))

    pending = sorted(pending, key=partial_key)
    return render_template('pharmacy/prescriptions.html', title='Prescription Queue', prescriptions=pending)


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
    # Expired batches are never dispensable, whatever their quantity.
    today = date.today()
    inv = PharmacyInventory.query.filter(
        PharmacyInventory.medication_id == item.medication_id,
        PharmacyInventory.quantity > 0,
        db.or_(PharmacyInventory.expiry_date.is_(None),
               PharmacyInventory.expiry_date >= today),
    ).order_by(
        PharmacyInventory.expiry_date.asc().nulls_last()
    ).all()

    available = sum(i.quantity for i in inv)
    med_name = item.medication.generic_name if item.medication else 'medication'
    if available <= 0:
        expired_only = PharmacyInventory.query.filter(
            PharmacyInventory.medication_id == item.medication_id,
            PharmacyInventory.quantity > 0).count() > 0
        flash(f'No dispensable stock for {med_name}'
              + (' (only expired batches remain).' if expired_only else '.'), 'danger')
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
        # Atomic conditional decrement: only succeeds if the batch still has at
        # least `take` units, preventing a concurrent dispense from creating a
        # lost update or negative inventory (Phase 27). If another transaction
        # has already depleted this batch, zero rows match and we skip it.
        from sqlalchemy import update as sql_update
        res = db.session.execute(
            sql_update(PharmacyInventory)
            .where(PharmacyInventory.id == stock.id,
                   PharmacyInventory.quantity >= take)
            .values(quantity=PharmacyInventory.quantity - take)
        )
        if res.rowcount != 1:
            db.session.rollback()
            flash('Stock changed by another pharmacist while dispensing. '
                  'Please review inventory and retry.', 'danger')
            return redirect(url_for('pharmacy.prescriptions'))
        # Reload the batch with a fresh DB read so quantity_after reflects the
        # post-decrement value (Session.get returns the identity-map object, so
        # force a database refresh with populate_existing).
        stock = db.session.get(PharmacyInventory, stock.id,
                               populate_existing=True)
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
                       f'Your prescription #{rx.id} has been fully dispensed and is ready for pickup.',
                       entity_type='prescription', entity_id=rx.id)
        from app.services import tasks as task_svc
        task_svc.complete_for_resource('prescription', rx.id, 'Fully dispensed')
        record_event(rx.patient_id, 'DISPENSE',
                     f'Prescription dispensed fully (#{rx.id})',
                     f'{med_name} · {dispensed}',
                     source_type='prescription', source_id=rx.id,
                     department='Pharmacy')
    else:
        record_event(rx.patient_id, 'DISPENSE',
                     f'Prescription dispensed ({med_name})',
                     f'{dispensed} unit(s){" · partial" if partial else ""} of {quantity} ordered',
                     source_type='prescription', source_id=rx.id,
                     department='Pharmacy')
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
    """Reject an Active prescription with a reason, notifying the physician."""
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
    from app.services import tasks as task_svc
    task_svc.cancel_for_resource('prescription', rx.id, f'Rejected by pharmacy: {reason}')
    record_event(rx.patient_id, 'PRESCRIPTION', f'Prescription #{rx.id} rejected by pharmacy',
                 reason, source_type='prescription', source_id=rx.id, department='Pharmacy')
    if rx.doctor:
        notify_doctor(rx.doctor, f'Prescription #{rx.id} rejected',
                      f'Your prescription #{rx.id} was rejected by pharmacy: {reason}',
                      notification_type='critical' if 'interaction' in reason.lower() else 'in-app',
                      entity_type='prescription', entity_id=rx.id)
    from app.services.notifications import notify_patient
    notify_patient(rx.patient, 'Prescription rejected',
                   f'Your prescription #{rx.id} could not be dispensed. Please contact your physician.',
                   entity_type='prescription', entity_id=rx.id)
    db.session.commit()
    flash('Prescription rejected; prescribing physician notified.', 'warning')
    return redirect(url_for('pharmacy.prescriptions'))


@pharmacy_bp.route('/prescriptions/<int:rx_id>')
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
def prescription_detail(rx_id):
    rx = Prescription.query.get_or_404(rx_id)
    return render_template('pharmacy/prescription_detail.html', title='Prescription Detail', rx=rx)


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
        flash(f'Adjustment would make stock negative (current {inv.quantity}). '
              'Enter a smaller withdrawal.', 'danger')
        return redirect(url_for('pharmacy.inventory', _anchor=f'batch-{inv.id}'))
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
    return render_template('pharmacy/transactions.html', title='Stock Transactions', txs=txs)


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
    return render_template('pharmacy/medications.html', title='Medication Catalog', medications=meds, search=search)


@pharmacy_bp.route('/ai-workbench')
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('MEDICATION_REVIEW')
def ai_workbench():
    """Clinical Pharmacist AI workbench:
    patients with active/pending prescriptions ready for a medication review."""
    rows = (db.session.query(Patient, Prescription)
            .join(Prescription, Prescription.patient_id == Patient.id)
            .filter(Prescription.status != 'Dispensed')
            .order_by(Prescription.prescribed_date.desc())
            .all())
    cases = []
    seen = set()
    for patient, rx in rows:
        if patient.id in seen:
            continue
        seen.add(patient.id)
        active_items = [i for i in rx.items if i.status != 'Cancelled']
        cases.append({
            'patient': patient,
            'pending_prescriptions': Prescription.query.filter_by(
                patient_id=patient.id).filter(Prescription.status != 'Dispensed').count(),
            'active_items': len(active_items),
            'latest_rx': rx,
        })
    total_active = Prescription.query.filter(Prescription.status != 'Dispensed').count()
    return render_template('pharmacy/ai_workbench.html', title='Clinical Pharmacist AI', cases=cases,
                           total_active=total_active)


@pharmacy_bp.route('/reconciliations')
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('RECONCILIATION_VIEW')
def reconciliations():
    from app.models import MedicationReconciliation
    items = MedicationReconciliation.query.order_by(
        MedicationReconciliation.created_at.desc()).limit(100).all()
    return render_template('pharmacy/reconciliations.html', title='Medication Reconciliations', items=items)


@pharmacy_bp.route('/patient/<int:patient_id>/reconcile', methods=['GET', 'POST'])
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('RECONCILIATION_CREATE')
def reconcile(patient_id):
    """Create a medication reconciliation: home list vs active prescriptions."""
    from app.access import require_patient_access
    patient = Patient.query.get_or_404(patient_id)
    require_patient_access(patient)
    from app.services.reconciliation import (run_reconciliation,
                                             normalize_home_medications)
    from app.models import MedicationReconciliation
    existing = MedicationReconciliation.query.filter_by(
        patient_id=patient_id, status='Open').first()
    if request.method == 'POST':
        if existing:
            flash('An open reconciliation already exists for this patient.', 'info')
            return redirect(url_for('pharmacy.reconciliations'))
        home_raw = request.form.get('home_medications') or ''
        home_list = normalize_home_medications(home_raw)
        rec, discrepancies = run_reconciliation(
            patient_id=patient_id,
            pharmacist_id=current_user.id,
            home_medications=home_list,
            reconciliation_type=request.form.get('reconciliation_type') or 'Admission',
            summary=request.form.get('summary') or None,
        )
        record_event(patient_id, 'MESSAGE',
                     'Medication reconciliation performed',
                     f'{len(discrepancies)} finding(s)',
                     source_type='reconciliation', source_id=rec.id,
                     department='Pharmacy')
        for d in discrepancies:
            if d.severity in ('HIGH', 'CRITICAL'):
                _atype = {'INTERACTION': 'DRUG_INTERACTION',
                          'ALLERGY': 'ALLERGY'}.get(
                    d.discrepancy_type, 'DUPLICATE_THERAPY')
                alert_svc.ensure_open_alert(
                    patient_id, _atype, severity=d.severity.upper(),
                    title=f'{d.discrepancy_type.replace("_", " ")}: {d.description[:90]}',
                    message=d.recommended_action,
                    source_type='reconciliation', source_id=rec.id)
        log_activity('CREATE_RECONCILIATION', 'reconciliation', rec.id,
                     f'patient={patient_id} findings={len(discrepancies)}')
        db.session.commit()
        flash(f'Reconciliation created with {len(discrepancies)} finding(s).', 'success')
        return redirect(url_for('pharmacy.reconciliation_detail', rec_id=rec.id))
    return render_template('pharmacy/reconcile.html', title='New Reconciliation', patient=patient,
                           existing=existing,
                           **patient_safety_context(patient.id),
                           today=utcnow().date())


@pharmacy_bp.route('/reconciliations/<int:rec_id>')
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('RECONCILIATION_VIEW')
def reconciliation_detail(rec_id):
    from app.models import MedicationReconciliation
    rec = MedicationReconciliation.query.get_or_404(rec_id)
    return render_template('pharmacy/reconciliation_detail.html', title='Reconciliation Detail', rec=rec)


@pharmacy_bp.route('/reconciliations/<int:rec_id>/complete', methods=['POST'])
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('RECONCILIATION_COMPLETE')
def complete_reconciliation(rec_id):
    from app.models import MedicationReconciliation
    from app.services.reconciliation import complete_reconciliation as mark_done
    rec = MedicationReconciliation.query.get_or_404(rec_id)
    mark_done(rec, request.form.get('notes'))
    log_activity('COMPLETE_RECONCILIATION', 'reconciliation', rec.id)
    db.session.commit()
    flash('Reconciliation completed.', 'success')
    return redirect(url_for('pharmacy.reconciliation_detail', rec_id=rec.id))


@pharmacy_bp.route('/discrepancies/<int:d_id>/resolve', methods=['POST'])
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('RECONCILIATION_COMPLETE')
def resolve_discrepancy(d_id):
    from app.models import ReconciliationDiscrepancy
    d = ReconciliationDiscrepancy.query.get_or_404(d_id)
    d.status = 'Resolved'
    d.resolved_note = request.form.get('resolved_note') or d.resolved_note
    db.session.commit()
    log_activity('RESOLVE_DISCREPANCY', 'reconciliation_discrepancy', d.id)
    flash('Discrepancy resolved.', 'success')
    return redirect(url_for('pharmacy.reconciliation_detail', rec_id=d.reconciliation_id))


# ------------------------- Pharmacist interventions -------------------------
@pharmacy_bp.route('/interventions')
@login_required
@roles_required('Pharmacist', 'Doctor', 'Admin', 'SuperAdmin')
@permissions_required('INTERVENTION_VIEW')
def interventions():
    from app.models import PharmacyIntervention
    items = PharmacyIntervention.query.order_by(
        PharmacyIntervention.created_at.desc()).limit(100).all()
    return render_template('pharmacy/interventions.html', title='Pharmacy Interventions', items=items)


@pharmacy_bp.route('/prescriptions/<int:rx_id>/intervene', methods=['POST'])
@login_required
@roles_required('Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('INTERVENTION_CREATE')
def create_intervention(rx_id):
    """Raise a clinical intervention on a prescription without mutating it."""
    from app.models import PharmacyIntervention
    from app.access import require_patient_access
    rx = Prescription.query.get_or_404(rx_id)
    require_patient_access(rx.patient)
    issue = (request.form.get('issue') or '').strip()
    if not issue:
        flash('Describe the medication issue.', 'warning')
        return redirect(url_for('pharmacy.prescription_detail', rx_id=rx.id))
    intervention = PharmacyIntervention(
        patient_id=rx.patient_id,
        prescription_id=rx.id,
        pharmacist_id=current_user.id,
        prescriber_id=rx.doctor.user_id if rx.doctor else None,
        issue=issue,
        severity=request.form.get('severity') or 'Moderate',
        recommendation=request.form.get('recommendation') or None,
        category=request.form.get('category') or 'OTHER',
        status='OPEN',
    )
    db.session.add(intervention)
    db.session.flush()
    record_event(rx.patient_id, 'MESSAGE',
                 f'Pharmacist intervention on Rx #{rx.id}',
                 f'{intervention.severity} · {issue[:120]}',
                 source_type='intervention', source_id=intervention.id,
                 department='Pharmacy')
    log_activity('CREATE_INTERVENTION', 'pharmacy_intervention', intervention.id,
                 f'rx={rx.id} severity={intervention.severity}')
    from app.services.notifications import notify
    if intervention.prescriber_id:
        notify(intervention.prescriber_id,
               f'Pharmacy intervention on Rx #{rx.id}',
               f'{issue} — please review the recommendation.',
               notification_type='critical' if intervention.severity in ('Major', 'Contraindicated') else 'in-app',
               entity_type='pharmacy_intervention', entity_id=intervention.id)
    else:
        notify_role('Doctor', f'Pharmacy intervention on Rx #{rx.id}',
                    f'{issue} — please review the recommendation.',
                    entity_type='pharmacy_intervention', entity_id=intervention.id)
    db.session.commit()
    flash('Intervention raised with the prescriber.', 'success')
    return redirect(url_for('pharmacy.prescription_detail', rx_id=rx.id))


@pharmacy_bp.route('/interventions/<int:i_id>/respond', methods=['POST'])
@login_required
@roles_required('Doctor', 'Pharmacist', 'Admin', 'SuperAdmin')
@permissions_required('INTERVENTION_RESPOND')
def respond_intervention(i_id):
    from app.models import PharmacyIntervention
    from app.access import require_patient_access
    intervention = PharmacyIntervention.query.get_or_404(i_id)
    require_patient_access(intervention.patient)
    status = request.form.get('status')
    if status not in ('ACCEPTED', 'REJECTED', 'RESOLVED'):
        flash('Invalid response status.', 'warning')
        return redirect(url_for('pharmacy.interventions'))
    intervention.status = status
    intervention.response = request.form.get('response') or intervention.response
    log_activity('RESPOND_INTERVENTION', 'pharmacy_intervention', intervention.id,
                 f'{status}')
    if intervention.pharmacist_id and intervention.pharmacist_id != current_user.id:
        from app.services.notifications import notify
        notify(intervention.pharmacist_id,
               f'Intervention {status.lower()} (Rx #{intervention.prescription_id})',
               f'{current_user.full_name} responded: {(intervention.response or status)[:160]}',
               entity_type='pharmacy_intervention', entity_id=intervention.id)
    if status == 'ACCEPTED':
        alert_svc.ensure_open_alert(
            intervention.patient_id, 'DUPLICATE_THERAPY', severity='INFO',
            title=f'Pharmacist recommendation accepted (Rx #{intervention.prescription_id})',
            message=intervention.recommendation,
            source_type='pharmacy_intervention', source_id=intervention.id)
    db.session.commit()
    flash(f'Intervention {status}.', 'success')
    return redirect(url_for('pharmacy.interventions'))


# ------------------------- Formulary interactions -------------------------
@pharmacy_bp.route('/drug-check', methods=['GET', 'POST'])
@login_required
@roles_required('Pharmacist', 'Doctor', 'Nurse', 'Admin', 'SuperAdmin')
@permissions_required('DRUG_INTERACTION')
def drug_check():
    """Standalone drug-drug interaction checker over the formulary."""
    meds = Medication.query.filter_by(is_active=True).order_by(
        Medication.generic_name).all()
    result = None
    if request.method == 'POST':
        try:
            ids = [int(x) for x in request.form.getlist('medications')]
        except (TypeError, ValueError):
            ids = []
        if len(ids) < 2:
            result = {'note': 'Select at least two medications to check for interactions.',
                      'interactions': [], 'count': 0}
        else:
            from app.services.ai import AIDrugInteractionEngine
            result = AIDrugInteractionEngine().check_interactions(ids)
            log_activity('DRUG_INTERACTION_CHECK', 'medication', None,
                         f'{len(ids)} medications checked, {result.get("count", 0)} interactions')
            db.session.commit()
    return render_template('pharmacy/drug_check.html', title='Drug Interaction Check', medications=meds, result=result)
