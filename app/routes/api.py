"""RESTful API endpoints for iHIS (JSON).

Public read endpoints exist for catalogue data (doctors, specialties, lab tests,
imaging types, medications). Patient-scoped clinical endpoints and all write
operations require an authenticated user with clinical/admin roles.
"""
from datetime import datetime, date

from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user

from app import db, csrf
from app.models import (
    Patient, Doctor, Specialty, LabTestCatalog, ImagingType, Medication,
    Appointment, LabOrder, LabResult, RadiologyOrder, RadiologyReport,
    MedicalRecord, Prescription, PrescriptionItem, PharmacyInventory, Referral, User,
)


api_bp = Blueprint('api', __name__)


def _iso(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _is_clinical_staff():
    return current_user.has_any_role(
        'Doctor', 'Nurse', 'Physiotherapist', 'Dentist', 'LabTechnician',
        'Radiologist', 'Pharmacist', 'Receptionist', 'Admin', 'SuperAdmin',
    )


def _require_patient_access(patient):
    """Allow self-access (patient) or any clinical/admin staff."""
    if current_user.user_type == 'patient' and patient:
        return current_user.id == patient.user_id
    if _is_clinical_staff():
        return True
    return False


def _doctor_of(user):
    return Doctor.query.filter_by(user_id=user.id).first()


def _patient_json(p):
    return {
        'id': p.id,
        'user_id': p.user_id,
        'full_name': p.user.full_name if p.user else None,
        'email': p.user.email if p.user else None,
        'date_of_birth': _iso(p.date_of_birth),
        'gender': p.gender,
        'phone': p.phone,
        'address': p.address,
        'blood_type': p.blood_type,
        'allergies': p.allergies,
        'chronic_diseases': p.chronic_diseases,
        'emergency_contact': p.emergency_contact,
    }


def _prescription_json(rx):
    return {
        'id': rx.id,
        'patient_id': rx.patient_id,
        'doctor_id': rx.doctor_id,
        'medication': rx.items[0].medication.generic_name if rx.items and rx.items[0].medication else None,
        'dosage': rx.items[0].dosage if rx.items else None,
        'frequency': rx.items[0].frequency if rx.items else None,
        'duration': rx.items[0].duration if rx.items else None,
        'instructions': rx.items[0].instructions if rx.items else None,
        'items': [{
            'id': i.id,
            'medication': i.medication.generic_name if i.medication else None,
            'dosage': i.dosage,
            'frequency': i.frequency,
            'duration': i.duration,
            'quantity': i.quantity,
            'status': i.status,
        } for i in rx.items],
        'refills': rx.refills,
        'status': rx.status,
        'prescribed_date': _iso(rx.prescribed_date),
    }


def _lab_order_json(o):
    return {
        'id': o.id,
        'patient_id': o.patient_id,
        'test': o.test.test_name if o.test else None,
        'category': o.test.category if o.test else None,
        'status': o.status,
        'priority': o.priority,
        'order_date': _iso(o.order_date),
        'result': {
            'value': o.result.result_value if o.result else None,
            'is_abnormal': o.result.is_abnormal if o.result else False,
            'notes': o.result.result_notes if o.result else None,
        } if o.result else None,
    }


def _radiology_order_json(o):
    return {
        'id': o.id,
        'patient_id': o.patient_id,
        'imaging_type': o.imaging_type.name if o.imaging_type else None,
        'status': o.status,
        'priority': o.priority,
        'order_date': _iso(o.order_date),
        'report': {
            'findings': o.report.findings if o.report else None,
            'impression': o.report.impression if o.report else None,
            'recommendation': o.report.recommendation if o.report else None,
        } if o.report else None,
    }


@api_bp.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': 'iHIS'})


@api_bp.route('/doctors')
def doctors():
    search = request.args.get('q', '')
    query = Doctor.query
    if search:
        query = query.filter(Doctor.license_number.ilike(f'%{search}%'))
    result = []
    for d in query.all():
        result.append({
            'id': d.id,
            'user_id': d.user_id,
            'name': d.user.full_name if d.user else None,
            'specialty': d.specialty.name if d.specialty else None,
            'license_number': d.license_number,
            'consultation_fee': d.consultation_fee,
        })
    return jsonify({'doctors': result})


@api_bp.route('/specialties')
def specialties():
    return jsonify({'specialties': [s.name for s in Specialty.query.all()]})


@api_bp.route('/lab-tests')
def lab_tests():
    tests = [{'id': t.id, 'name': t.test_name, 'category': t.category,
              'normal_range': t.normal_range, 'unit': t.unit, 'price': t.price}
             for t in LabTestCatalog.query.all()]
    return jsonify({'tests': tests})


@api_bp.route('/imaging-types')
def imaging_types():
    return jsonify({'imaging': [{'id': i.id, 'name': i.name, 'price': i.price}
                                for i in ImagingType.query.all()]})


@api_bp.route('/medications')
def medications():
    return jsonify({'medications': [{'id': m.id, 'generic_name': m.generic_name,
                                     'brand_name': m.brand_name, 'category': m.category}
                                    for m in Medication.query.filter_by(is_active=True).all()]})


@api_bp.route('/appointments')
@login_required
def appointments():
    """List appointments (paginated, optional status filter)."""
    status = request.args.get('status', '').strip()
    query = Appointment.query
    if current_user.user_type == 'patient':
        query = query.filter_by(patient_id=current_user.patient_profile.id)
    if status:
        query = query.filter_by(status=status)
    items = query.order_by(Appointment.scheduled_at.desc()).limit(100).all()
    return jsonify({'appointments': [{
        'id': a.id,
        'patient_id': a.patient_id,
        'patient': a.patient.user.full_name if a.patient and a.patient.user else None,
        'doctor_id': a.doctor_id,
        'doctor': a.doctor.user.full_name if a.doctor and a.doctor.user else None,
        'scheduled_at': _iso(a.scheduled_at),
        'status': a.status,
        'duration_minutes': a.duration_minutes,
        'priority': a.priority,
        'reason': a.reason,
    } for a in items]})


@api_bp.route('/appointments', methods=['POST'])
@csrf.exempt
@login_required
def create_appointment():
    """Create an appointment. JSON body: patient_id, doctor_id, scheduled_at, priority, reason."""
    data = request.get_json(silent=True) or {}
    patient_id = data.get('patient_id')
    doctor_id = data.get('doctor_id')
    scheduled_at = data.get('scheduled_at')
    if not (patient_id and doctor_id and scheduled_at):
        return jsonify({'error': 'patient_id, doctor_id and scheduled_at are required'}), 400
    try:
        scheduled = datetime.fromisoformat(scheduled_at)
    except ValueError:
        return jsonify({'error': 'scheduled_at must be ISO 8601'}), 400
    a = Appointment(
        patient_id=int(patient_id),
        doctor_id=int(doctor_id),
        scheduled_at=scheduled,
        status=data.get('status', 'Scheduled'),
        priority=data.get('priority', 'Normal'),
        reason=data.get('reason', ''),
        created_by=current_user.id,
    )
    db.session.add(a)
    db.session.commit()
    return jsonify({'id': a.id, 'status': a.status}), 201


@api_bp.route('/patients')
def patients():
    result = []
    for p in Patient.query.limit(200).all():
        result.append(_patient_json(p))
    return jsonify({'patients': result})


@api_bp.route('/patients/<int:patient_id>')
@login_required
def patient_detail(patient_id):
    p = Patient.query.get_or_404(patient_id)
    if not _require_patient_access(p):
        abort(403)
    return jsonify(_patient_json(p))


@api_bp.route('/patients/<int:patient_id>/records')
@login_required
def patient_records(patient_id):
    p = Patient.query.get_or_404(patient_id)
    if not _require_patient_access(p):
        abort(403)
    records = [{
        'id': r.id,
        'visit_date': _iso(r.visit_date),
        'diagnosis': r.diagnosis,
        'treatment_plan': r.treatment_plan,
        'clinical_notes': r.clinical_notes,
        'doctor': r.doctor.user.full_name if r.doctor and r.doctor.user else None,
    } for r in p.medical_records]
    return jsonify({'records': records})


@api_bp.route('/patients/<int:patient_id>/prescriptions')
@login_required
def patient_prescriptions(patient_id):
    p = Patient.query.get_or_404(patient_id)
    if not _require_patient_access(p):
        abort(403)
    return jsonify({'prescriptions': [_prescription_json(rx) for rx in p.prescriptions]})


@api_bp.route('/patients/<int:patient_id>/lab-orders')
@login_required
def patient_lab_orders(patient_id):
    p = Patient.query.get_or_404(patient_id)
    if not _require_patient_access(p):
        abort(403)
    return jsonify({'lab_orders': [_lab_order_json(o) for o in p.lab_orders]})


@api_bp.route('/patients/<int:patient_id>/radiology-orders')
@login_required
def patient_radiology_orders(patient_id):
    p = Patient.query.get_or_404(patient_id)
    if not _require_patient_access(p):
        abort(403)
    return jsonify({'radiology_orders': [_radiology_order_json(o) for o in p.radiology_orders]})


@api_bp.route('/prescriptions')
@login_required
def prescriptions():
    """List prescriptions (optional patient_id, status filters)."""
    query = Prescription.query
    patient_id = request.args.get('patient_id', type=int)
    status = request.args.get('status', '').strip()
    if current_user.user_type == 'patient':
        query = query.filter_by(patient_id=current_user.patient_profile.id)
    elif patient_id:
        query = query.filter_by(patient_id=patient_id)
    if status:
        query = query.filter_by(status=status)
    items = query.order_by(Prescription.prescribed_date.desc()).limit(100).all()
    return jsonify({'prescriptions': [_prescription_json(rx) for rx in items]})


@api_bp.route('/prescriptions', methods=['POST'])
@csrf.exempt
@login_required
def create_prescription():
    """Create a prescription. JSON body: patient_id, refills, and items:
    [{"medication_id":1,"dosage":"500mg","frequency":"Twice daily",
      "duration":"7 days","instructions":"...","quantity":1}].
    For backward compatibility a single medication_id/dosage/... is also accepted."""
    data = request.get_json(silent=True) or {}
    doctor = _doctor_of(current_user)
    patient_id = data.get('patient_id')
    items_data = data.get('items')
    if not doctor:
        return jsonify({'error': 'Only doctors can create prescriptions'}), 403
    if not patient_id:
        return jsonify({'error': 'patient_id is required'}), 400
    if not items_data:
        if data.get('medication_id'):
            items_data = [{
                'medication_id': data.get('medication_id'),
                'dosage': data.get('dosage', ''),
                'frequency': data.get('frequency', ''),
                'duration': data.get('duration', ''),
                'instructions': data.get('instructions', ''),
                'quantity': data.get('quantity', 1),
            }]
        else:
            return jsonify({'error': 'at least one medication is required'}), 400
    if not isinstance(items_data, list) or not items_data:
        return jsonify({'error': 'items must be a non-empty list'}), 400

    rx = Prescription(
        patient_id=int(patient_id),
        doctor_id=doctor.id,
        refills=int(data.get('refills', 0) or 0),
        status=data.get('status', 'Active'),
    )
    db.session.add(rx)
    db.session.flush()
    for it in items_data:
        mid = it.get('medication_id')
        if not mid:
            continue
        try:
            qty = max(1, int(it.get('quantity') or 1))
        except (TypeError, ValueError):
            qty = 1
        db.session.add(PrescriptionItem(
            prescription_id=rx.id,
            medication_id=int(mid),
            dosage=it.get('dosage', ''),
            frequency=it.get('frequency', ''),
            duration=it.get('duration', ''),
            instructions=it.get('instructions', ''),
            quantity=qty,
        ))
    db.session.commit()
    return jsonify({'id': rx.id, 'status': rx.status}), 201


@api_bp.route('/lab-orders/<int:order_id>')
@login_required
def lab_order(order_id):
    o = LabOrder.query.get_or_404(order_id)
    if not _require_patient_access(o.patient):
        abort(403)
    return jsonify(_lab_order_json(o))


@api_bp.route('/radiology-orders/<int:order_id>')
@login_required
def radiology_order(order_id):
    o = RadiologyOrder.query.get_or_404(order_id)
    if not _require_patient_access(o.patient):
        abort(403)
    return jsonify(_radiology_order_json(o))


@api_bp.route('/referrals')
@login_required
def referrals():
    if current_user.user_type == 'patient':
        abort(403)
    items = Referral.query.order_by(Referral.created_at.desc()).limit(100).all()
    return jsonify({'referrals': [{
        'id': r.id,
        'patient_id': r.patient_id,
        'patient': r.patient.user.full_name if r.patient and r.patient.user else None,
        'to_specialty': r.to_specialty,
        'reason': r.reason,
        'status': r.status,
        'created_at': _iso(r.created_at),
    } for r in items]})


@api_bp.route('/referrals', methods=['POST'])
@csrf.exempt
@login_required
def create_referral():
    """Create a referral. JSON body: patient_id, to_specialty, reason, to_doctor_id."""
    data = request.get_json(silent=True) or {}
    doctor = _doctor_of(current_user)
    patient_id = data.get('patient_id')
    to_specialty = data.get('to_specialty')
    if not doctor:
        return jsonify({'error': 'Only doctors can create referrals'}), 403
    if not (patient_id and to_specialty):
        return jsonify({'error': 'patient_id and to_specialty are required'}), 400
    r = Referral(
        patient_id=int(patient_id),
        from_doctor_id=doctor.id,
        to_doctor_id=int(data['to_doctor_id']) if data.get('to_doctor_id') else None,
        to_specialty=to_specialty,
        reason=data.get('reason', ''),
        status=data.get('status', 'Pending'),
    )
    db.session.add(r)
    db.session.commit()
    return jsonify({'id': r.id, 'status': r.status}), 201


@api_bp.route('/inventory')
@login_required
def inventory():
    if not current_user.has_any_role('Pharmacist', 'Admin', 'SuperAdmin'):
        abort(403)
    items = PharmacyInventory.query.order_by(PharmacyInventory.quantity).all()
    return jsonify({'inventory': [{
        'id': i.id,
        'medication': i.medication.generic_name if i.medication else None,
        'quantity': i.quantity,
        'reorder_level': i.reorder_level,
        'unit_cost': i.unit_cost,
        'selling_price': i.selling_price,
        'expiry_date': _iso(i.expiry_date),
        'batch_number': i.batch_number,
        'low_stock': i.quantity <= i.reorder_level,
    } for i in items]})


@api_bp.route('/users/me')
@login_required
def me():
    u = current_user
    return jsonify({
        'id': u.id,
        'username': u.username,
        'email': u.email,
        'full_name': u.full_name,
        'user_type': u.user_type,
        'phone': u.phone,
        'roles': [r.name for r in u.roles],
        'patient_id': u.patient_profile.id if u.patient_profile else None,
        'doctor_id': u.doctor_profile.id if u.doctor_profile else None,
        'last_login': _iso(u.last_login),
    })