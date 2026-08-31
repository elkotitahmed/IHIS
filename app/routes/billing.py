"""Billing portal: bills, line items, and payments (revenue cycle).

Ties into the rest of iHIS Core: completed clinical services (verified lab
results, signed radiology reports, dispensed prescriptions, consultations)
flow into bills automatically so the front desk never re-keys pricing.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from datetime import date, timedelta
from app import db
from app.models import (
    Bill, BillItem, Payment, ServiceCatalog, Patient, User,
    LabOrder, RadiologyOrder, Prescription, PrescriptionItem,
    PharmacyInventory, Doctor,
)
from app.routes.decorators import roles_required, permissions_required, log_activity
from app.utils import utcnow

billing_bp = Blueprint('billing', __name__)

STAFF = ('Receptionist', 'Admin', 'SuperAdmin', 'Cashier')


@billing_bp.route('/dashboard')
@login_required
@roles_required(*STAFF)
@permissions_required('BILL_VIEW')
def dashboard():
    today = date.today()
    bills = Bill.query.filter(Bill.status != 'Voided').all()
    outstanding = sum(b.balance() for b in bills)
    unpaid = Bill.query.filter(Bill.status.in_(['Unpaid', 'PartiallyPaid'])).count()
    paid_today = Payment.query.filter(
        db.func.date(Payment.received_at) == today,
        Payment.received_at.isnot(None)).join(Bill).filter(Bill.status != 'Voided').all()
    collected_today = sum(p.amount for p in paid_today)
    recent_bills = Bill.query.order_by(Bill.issued_at.desc()).limit(8).all()
    recent_payments = Payment.query.order_by(Payment.received_at.desc()).limit(8).all()
    return render_template('billing/dashboard.html', title='Billing Dashboard',
                           outstanding=outstanding, unpaid=unpaid,
                           collected_today=collected_today,
                           recent_bills=recent_bills, recent_payments=recent_payments,
                           today=today)


@billing_bp.route('/bills')
@login_required
@roles_required(*STAFF)
@permissions_required('BILL_VIEW')
def bills():
    query = Bill.query
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    if q:
        query = query.join(Patient).join(User).filter(
            (Bill.bill_no.ilike(f'%{q}%')) |
            (User.full_name.ilike(f'%{q}%')) |
            (User.email.ilike(f'%{q}%'))
        )
    if status:
        query = query.filter(Bill.status == status)
    items = query.order_by(Bill.issued_at.desc()).all()
    return render_template('billing/bills.html', title='Bills', items=items,
                           status=status, q=q)


def _next_bill_no():
    last = Bill.query.order_by(Bill.id.desc()).first()
    return f'INV-{1000 + (last.id + 1 if last else 1)}'


@billing_bp.route('/bills/new', methods=['GET', 'POST'])
@login_required
@roles_required(*STAFF)
@permissions_required('BILL_CREATE')
def new_bill():
    if request.method == 'POST':
        patient_id = request.form.get('patient_id')
        discount = float(request.form.get('discount') or 0)
        tax = float(request.form.get('tax_percent') or 0)
        notes = request.form.get('notes')
        patient = db.session.get(Patient, int(patient_id)) if patient_id else None
        if not patient:
            flash('Please select a patient.', 'danger')
            return redirect(url_for('billing.new_bill'))

        descs = request.form.getlist('desc')
        qtys = request.form.getlist('qty')
        prices = request.form.getlist('price')
        lines = []
        for i, d in enumerate(descs):
            d = (d or '').strip()
            if not d:
                continue
            qty = int(qtys[i]) if i < len(qtys) and qtys[i] else 1
            price = float(prices[i]) if i < len(prices) and prices[i] else 0
            lines.append((d, qty, price))
        if not lines:
            flash('Add at least one line item.', 'warning')
            return redirect(url_for('billing.new_bill', patient_id=patient_id))

        bill = Bill(patient_id=patient.id, created_by=current_user.id,
                    discount=discount, tax_percent=tax, notes=notes,
                    bill_no=_next_bill_no(), source_type='Manual')
        db.session.add(bill)
        db.session.flush()
        for d, qty, price in lines:
            db.session.add(BillItem(bill_id=bill.id, description=d,
                                    quantity=qty, unit_price=price))
        db.session.flush()
        log_activity('CREATE_BILL', 'bill', bill.id,
                     f'patient={patient.id} total={bill.total():.2f}')
        from app.services.notifications import notify_patient
        notify_patient(patient, 'New bill issued',
                       f'Bill {bill.bill_no} for {bill.total():.2f} has been issued to you.')
        db.session.commit()
        flash(f'Bill {bill.bill_no} created. Total {bill.total():.2f}.', 'success')
        return redirect(url_for('billing.view_bill', bill_id=bill.id))

    patient_id = request.args.get('patient_id')
    sel = db.session.get(Patient, int(patient_id)) if patient_id else None
    # Pre-populate the bill from the patient's completed, not-yet-billed services.
    preselected = []
    if sel:
        preselected = _completed_services_for(sel.id)
    patients = Patient.query.all()
    return render_template('billing/new_bill.html', title='New Bill',
                           patients=patients, sel=sel, preselected=preselected,
                           today=date.today())


def _completed_services_for(patient_id):
    """Return priced services the patient has consumed but not yet billed."""
    services = []
    for o in LabOrder.query.filter_by(patient_id=patient_id, status='Completed').all():
        if o.result and o.result.status in ('Verified', 'Locked'):
            if not _bill_exists('Lab', o.id):
                services.append({'label': f'Lab — {o.test.test_name}', 'qty': 1,
                                 'price': o.test.price or 0})
    for o in RadiologyOrder.query.filter_by(patient_id=patient_id, status='Completed').all():
        if o.report and o.report.status in ('Signed', 'Locked'):
            if not _bill_exists('Radiology', o.id):
                services.append({'label': f'Radiology — {o.imaging_type.name}', 'qty': 1,
                                 'price': o.imaging_type.price or 0})
    for rx in Prescription.query.filter_by(patient_id=patient_id, status='Dispensed').all():
        if not _bill_exists('Pharmacy', rx.id):
            total = 0.0
            for it in rx.items:
                inv = PharmacyInventory.query.filter_by(medication_id=it.medication_id).first()
                total += (inv.selling_price or 0) * (it.quantity or 1)
            if total:
                services.append({'label': f'Medications — Rx #{rx.id}', 'qty': 1,
                                 'price': total})
    return services


def _bill_exists(source_type, source_id):
    return Bill.query.filter_by(source_type=source_type, source_id=source_id).first() is not None


@billing_bp.route('/bills/<int:bill_id>')
@login_required
@roles_required(*STAFF)
@permissions_required('BILL_VIEW')
def view_bill(bill_id):
    bill = Bill.query.get_or_404(bill_id)
    return render_template('billing/view_bill.html', title=f'Bill {bill.bill_no}',
                           bill=bill)


@billing_bp.route('/bills/<int:bill_id>/pay', methods=['POST'])
@login_required
@roles_required(*STAFF)
@permissions_required('PAYMENT_RECORD')
def record_payment(bill_id):
    bill = Bill.query.get_or_404(bill_id)
    if bill.status == 'Voided':
        flash('Cannot pay a voided bill.', 'warning')
        return redirect(url_for('billing.view_bill', bill_id=bill.id))
    try:
        amount = float(request.form.get('amount'))
    except (TypeError, ValueError):
        flash('Enter a valid payment amount.', 'danger')
        return redirect(url_for('billing.view_bill', bill_id=bill.id))
    if amount <= 0:
        flash('Payment amount must be greater than zero.', 'danger')
        return redirect(url_for('billing.view_bill', bill_id=bill.id))
    balance = bill.balance()
    if amount > balance + 0.001:
        flash('Payment exceeds the outstanding balance.', 'danger')
        return redirect(url_for('billing.view_bill', bill_id=bill.id))
    method = request.form.get('method') or 'Cash'
    reference = request.form.get('reference')
    last_pay = Payment.query.order_by(Payment.id.desc()).first()
    receipt_no = f'RCT-{10000 + (last_pay.id + 1 if last_pay else 1)}'
    db.session.add(Payment(bill_id=bill.id, amount=amount, method=method,
                           reference=reference, received_by=current_user.id,
                           receipt_no=receipt_no,
                           notes=request.form.get('notes')))
    bill.status = 'Paid' if amount + 0.001 >= bill.balance() else 'PartiallyPaid'
    log_activity('RECORD_PAYMENT', 'bill', bill.id,
                 f'amount={amount} method={method}')
    from app.services.notifications import notify_patient
    notify_patient(bill.patient, 'Payment received',
                   f'Payment of {amount:.2f} received on {bill.bill_no}. Remaining balance: {bill.balance():.2f}.')
    db.session.commit()
    flash(f'Payment of {amount:.2f} recorded (receipt {receipt_no}).', 'success')
    return redirect(url_for('billing.view_bill', bill_id=bill.id))


@billing_bp.route('/bills/<int:bill_id>/void', methods=['POST'])
@login_required
@roles_required('Admin', 'SuperAdmin')
@permissions_required('BILL_VOID')
def void_bill(bill_id):
    bill = Bill.query.get_or_404(bill_id)
    if bill.paid_amount() > 0:
        flash('Cannot void a bill that already received payments.', 'danger')
        return redirect(url_for('billing.view_bill', bill_id=bill.id))
    bill.status = 'Voided'
    log_activity('VOID_BILL', 'bill', bill.id, 'reason=' + (request.form.get('reason') or 'Not given'))
    db.session.commit()
    flash(f'Bill {bill.bill_no} voided.', 'success')
    return redirect(url_for('billing.bills'))


@billing_bp.route('/service-catalog')
@login_required
@roles_required(*STAFF)
@permissions_required('BILL_VIEW')
def service_catalog():
    items = ServiceCatalog.query.order_by(ServiceCatalog.category, ServiceCatalog.name).all()
    return render_template('billing/service_catalog.html', title='Service Catalog',
                           items=items)


@billing_bp.route('/service-catalog/add', methods=['POST'])
@login_required
@roles_required('Admin', 'SuperAdmin')
@permissions_required('SERVICE_CATALOG_MANAGE')
def add_service():
    name = request.form.get('name')
    if not name:
        flash('Service name is required.', 'danger')
        return redirect(url_for('billing.service_catalog'))
    db.session.add(ServiceCatalog(
        name=name, category=request.form.get('category') or 'General',
        price=float(request.form.get('price') or 0),
        is_active=False if request.form.get('service_is_active') is None else True))
    log_activity('ADD_SERVICE', 'service_catalog', 0, f'name={name}')
    db.session.commit()
    flash('Service added.', 'success')
    return redirect(url_for('billing.service_catalog'))


@billing_bp.route('/service-catalog/<int:sid>/toggle', methods=['POST'])
@login_required
@roles_required('Admin', 'SuperAdmin')
@permissions_required('SERVICE_CATALOG_MANAGE')
def toggle_service(sid):
    s = ServiceCatalog.query.get_or_404(sid)
    s.is_active = not s.is_active
    db.session.commit()
    flash(f"Service '{s.name}' {'activated' if s.is_active else 'deactivated'}.", 'success')
    return redirect(url_for('billing.service_catalog'))


@billing_bp.route('/reports')
@login_required
@roles_required(*STAFF)
@permissions_required('BILL_VIEW')
def reports():
    today = date.today()
    week = today - timedelta(days=6)
    payments = Payment.query.filter(Payment.received_at >= datetime_from(week)).all()
    by_day = {}
    for p in payments:
        key = p.received_at.date() if p.received_at else today
        by_day[key] = by_day.get(key, 0) + p.amount
    daily = [{'date': today - timedelta(days=i),
              'total': by_day.get(today - timedelta(days=i), 0.0)} for i in range(7)]
    daily.reverse()
    return render_template('billing/reports.html', title='Revenue Report', daily=daily)


def datetime_from(d):
    from datetime import datetime
    return datetime.combine(d, datetime.min.time())
