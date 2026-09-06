"""Care coordination blueprint: referrals, care teams, multidisciplinary cases."""
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from app import db
from app.models import (
    Referral, CareTeam, CareTeamMember, MultidisciplinaryCase,
    Patient, Doctor, User, Specialty,
)
from app.routes.decorators import roles_required, log_activity
from app.access import require_patient_access, patient_access_required
from app.services.timeline import record_event
from app.services.clinical_orders import (create_referral, transition_referral,
                                          normalize_referral_status, REFERRAL_TRANSITIONS)
from app.access import accessible_patient_ids, PHYSIO_KEYWORDS, DENTAL_KEYWORDS

care_bp = Blueprint('care', __name__)

CARE = ('Doctor', 'Nurse', 'Physiotherapist', 'Dentist', 'LabTechnician',
        'Radiologist', 'Pharmacist', 'Admin', 'SuperAdmin')


def _current_doctor():
    return Doctor.query.filter_by(user_id=current_user.id).first()


def _patient_or_404(patient_id):
    patient = Patient.query.filter_by(id=patient_id).first()
    if not patient:
        flash('Patient not found.', 'warning')
        return None
    return patient


# ------------------------- Referrals -------------------------
@care_bp.route('/referrals')
@login_required
@roles_required(*CARE)
def referrals():
    """Referral worklist.

    - Admin/SuperAdmin: everything.
    - Doctor: referrals they sent *or* that are addressed to them.
    - Physiotherapist / Dentist: referrals addressed to their discipline.
    - Other staff: referrals for patients they have access to.
    """
    q = Referral.query
    if current_user.has_any_role('Admin', 'SuperAdmin'):
        pass
    elif current_user.has_role('Doctor'):
        doc = _current_doctor()
        if doc:
            q = q.filter(db.or_(Referral.from_doctor_id == doc.id,
                                Referral.to_doctor_id == doc.id))
        else:
            q = q.filter(Referral.id == -1)
    elif current_user.has_role('Physiotherapist'):
        q = q.filter(db.or_(*[Referral.to_specialty.ilike(f'%{k}%') for k in PHYSIO_KEYWORDS]))
    elif current_user.has_role('Dentist'):
        q = q.filter(db.or_(*[Referral.to_specialty.ilike(f'%{k}%') for k in DENTAL_KEYWORDS]))
    else:
        pids = accessible_patient_ids(current_user)
        q = q.filter(Referral.patient_id.in_(sorted(pids) if pids else [-1]))
    f_status = request.args.get('status', '').strip()
    if f_status:
        q = q.filter(Referral.status == f_status)
    refs = q.order_by(Referral.created_at.desc()).limit(200).all()
    can_create = current_user.has_any_role('Doctor', 'Admin', 'SuperAdmin')
    pids = accessible_patient_ids(current_user) if can_create else set()
    patients = (Patient.query.join(User, Patient.user_id == User.id)
                .filter(Patient.id.in_(sorted(pids) if pids else [-1]))
                .order_by(User.full_name).all()) if can_create else []
    doctors = Doctor.query.join(User, Doctor.user_id == User.id).order_by(User.full_name).all()
    return render_template('care/referrals.html', title='Referrals', referrals=refs,
                           doctors=doctors, patients=patients,
                           specialties=Specialty.query.order_by(Specialty.name).all(),
                           f_status=f_status, can_create=can_create,
                           transitions=REFERRAL_TRANSITIONS,
                           normalize=normalize_referral_status)


@care_bp.route('/referrals/new', methods=['POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def new_referral():
    doc = _current_doctor()
    if not doc and not current_user.has_any_role('Admin', 'SuperAdmin'):
        flash('Only a physician can create a referral.', 'warning')
        return redirect(url_for('care.referrals'))
    patient_id = request.form.get('patient_id')
    to_doctor_id = request.form.get('to_doctor_id') or None
    to_specialty = request.form.get('to_specialty') or None
    reason = request.form.get('reason')
    urgency = (request.form.get('urgency') or 'Routine').strip()
    if urgency not in ('Routine', 'Urgent', 'Emergency'):
        urgency = 'Routine'
    if not patient_id or not reason:
        flash('Patient and reason are required.', 'warning')
        return redirect(url_for('care.referrals'))
    p = Patient.query.filter_by(id=int(patient_id)).first()
    if p is None:
        flash('Patient not found.', 'warning')
        return redirect(url_for('care.referrals'))
    require_patient_access(p)
    to_doctor = db.session.get(Doctor, int(to_doctor_id)) if to_doctor_id else None
    if not to_doctor and not to_specialty:
        flash('Choose a receiving physician or a specialty.', 'warning')
        return redirect(url_for('care.referrals'))
    ref = create_referral(p, doc, reason, to_specialty=to_specialty,
                          to_doctor=to_doctor, urgency=urgency,
                          notes=request.form.get('notes'))
    log_activity('CREATE_REFERRAL', 'referral', ref.id,
                 f'patient_id={patient_id} to={to_specialty or to_doctor_id} urgency={urgency}')
    db.session.commit()
    flash('Referral sent to the receiving provider.', 'success')
    return redirect(url_for('care.referrals'))


@care_bp.route('/referrals/<int:ref_id>/status', methods=['POST'])
@login_required
@roles_required(*CARE)
def update_referral_status(ref_id):
    ref = Referral.query.get_or_404(ref_id)
    require_patient_access(ref.patient)
    new_status = normalize_referral_status(request.form.get('status'))
    # Only the receiving side (or a supervisor) accepts/rejects/completes;
    # the referring doctor may only close their own referral.
    doc = _current_doctor()
    is_receiver = (
        current_user.has_any_role('Admin', 'SuperAdmin')
        or (doc and ref.to_doctor_id == doc.id)
        or (current_user.has_role('Physiotherapist') and any(
            k in (ref.to_specialty or '').lower() for k in PHYSIO_KEYWORDS))
        or (current_user.has_role('Dentist') and any(
            k in (ref.to_specialty or '').lower() for k in DENTAL_KEYWORDS))
        or (doc and ref.to_doctor_id is None and not any(
            k in (ref.to_specialty or '').lower() for k in PHYSIO_KEYWORDS + DENTAL_KEYWORDS))
    )
    is_sender = doc is not None and ref.from_doctor_id == doc.id
    if new_status == 'CLOSED' and not (is_receiver or is_sender):
        abort(403)
    if new_status != 'CLOSED' and not is_receiver:
        abort(403)
    try:
        transition_referral(ref, new_status, response=request.form.get('response'))
    except ValueError as exc:
        flash(str(exc), 'warning')
        return redirect(url_for('care.referrals'))
    log_activity('UPDATE_REFERRAL', 'referral', ref_id, new_status)
    db.session.commit()
    flash('Referral status updated.', 'success')
    return redirect(url_for('care.referrals'))


# ------------------------- Care Teams -------------------------
@care_bp.route('/teams/<int:patient_id>')
@login_required
@roles_required(*CARE)
@patient_access_required
def team(patient_id):
    patient = _patient_or_404(patient_id)
    if not patient:
        return redirect(url_for('main.dashboard'))
    team = CareTeam.query.filter_by(patient_id=patient_id).first()
    staff = User.query.filter(User.user_type.in_([
        'doctor', 'nurse', 'physiotherapist', 'dentist', 'lab_technician',
        'radiologist', 'pharmacist', 'receptionist'])).all()
    return render_template('care/team.html', title='Care Team', patient=patient,
                           team=team, staff=staff)


@care_bp.route('/teams/<int:patient_id>/add-member', methods=['POST'])
@login_required
@roles_required(*CARE)
@patient_access_required
def add_member(patient_id):
    patient = _patient_or_404(patient_id)
    if not patient:
        return redirect(url_for('main.dashboard'))
    user_id = request.form.get('user_id')
    role = request.form.get('role') or 'Care Team Member'
    user = User.query.filter_by(id=int(user_id)).first() if user_id else None
    if not user:
        flash('Select a valid staff member.', 'warning')
        return redirect(url_for('care.team', patient_id=patient_id))
    team = CareTeam.query.filter_by(patient_id=patient_id).first()
    if not team:
        team = CareTeam(patient_id=patient_id, name=f'Care Team - {patient.user.full_name}')
        db.session.add(team)
        db.session.flush()
    if not CareTeamMember.query.filter_by(team_id=team.id, user_id=user.id).first():
        db.session.add(CareTeamMember(team_id=team.id, user_id=user.id, role=role))
        log_activity('ADD_CARE_MEMBER', 'care_team', team.id, f'{user.full_name} as {role}')
        db.session.commit()
        flash(f'Added {user.full_name} to the care team.', 'success')
    else:
        flash('That member is already on the care team.', 'info')
    return redirect(url_for('care.team', patient_id=patient_id))


@care_bp.route('/teams/<int:patient_id>/remove-member/<int:member_id>', methods=['POST'])
@login_required
@roles_required(*CARE)
@patient_access_required
def remove_member(patient_id, member_id):
    member = CareTeamMember.query.get_or_404(member_id)
    if not member.team or member.team.patient_id != patient_id:
        flash('That member does not belong to this patient\'s care team.', 'warning')
        return redirect(url_for('care.team', patient_id=patient_id))
    db.session.delete(member)
    db.session.commit()
    flash('Member removed from care team.', 'success')
    return redirect(url_for('care.team', patient_id=patient_id))


# ------------------- Multidisciplinary Cases -------------------
@care_bp.route('/cases')
@login_required
@roles_required(*CARE)
def cases():
    pids = accessible_patient_ids(current_user)
    cases_list = (MultidisciplinaryCase.query
                  .filter(MultidisciplinaryCase.patient_id.in_(sorted(pids) if pids else [-1]))
                  .order_by(MultidisciplinaryCase.created_at.desc()).limit(200).all())
    patients = (Patient.query.join(User, Patient.user_id == User.id)
                .filter(Patient.id.in_(sorted(pids) if pids else [-1]))
                .order_by(User.full_name).all())
    return render_template('care/cases.html', title='Multidisciplinary Cases',
                           cases=cases_list, patients=patients)


@care_bp.route('/cases/new', methods=['POST'])
@login_required
@roles_required(*CARE)
def new_case():
    patient_id = request.form.get('patient_id')
    title = request.form.get('title')
    description = request.form.get('description')
    if not patient_id or not title:
        flash('Patient and title are required.', 'warning')
        return redirect(url_for('care.cases'))
    p = Patient.query.filter_by(id=int(patient_id)).first()
    if p is None:
        flash('Patient not found.', 'warning')
        return redirect(url_for('care.cases'))
    require_patient_access(p)
    case = MultidisciplinaryCase(patient_id=p.id, title=title,
                                 description=description, status='Open')
    db.session.add(case)
    db.session.flush()
    log_activity('CREATE_MDCASE', 'md_case', case.id, title)
    db.session.commit()
    flash('Multidisciplinary case opened.', 'success')
    return redirect(url_for('care.cases'))


@care_bp.route('/cases/<int:case_id>/status', methods=['POST'])
@login_required
@roles_required(*CARE)
def update_case_status(case_id):
    case = MultidisciplinaryCase.query.get_or_404(case_id)
    require_patient_access(case.patient)
    status = request.form.get('status')
    if status in ('Open', 'In Progress', 'Resolved', 'Closed'):
        case.status = status
        log_activity('UPDATE_MDCASE', 'md_case', case_id, status)
        db.session.commit()
        flash('Case status updated.', 'success')
    return redirect(url_for('care.cases'))
