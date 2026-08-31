"""Care coordination blueprint: referrals, care teams, multidisciplinary cases."""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app import db
from app.models import (
    Referral, CareTeam, CareTeamMember, MultidisciplinaryCase,
    Patient, Doctor, User, Specialty,
)
from app.routes.decorators import roles_required, log_activity
from app.access import require_patient_access, patient_access_required

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
    if current_user.has_any_role('Admin', 'SuperAdmin'):
        refs = Referral.query.order_by(Referral.created_at.desc()).all()
    else:
        doc = _current_doctor()
        if doc:
            refs = Referral.query.filter_by(from_doctor_id=doc.id)\
                .order_by(Referral.created_at.desc()).all()
        else:
            refs = []
    return render_template('care/referrals.html', title='Referrals', referrals=refs,
                           doctors=Doctor.query.all(), patients=Patient.query.all(),
                           specialties=Specialty.query.all())


@care_bp.route('/referrals/new', methods=['POST'])
@login_required
@roles_required('Doctor', 'Admin', 'SuperAdmin')
def new_referral():
    doc = _current_doctor()
    if not doc and not current_user.has_any_role('Admin', 'SuperAdmin'):
        flash('Only a doctor can create a referral.', 'warning')
        return redirect(url_for('care.referrals'))
    patient_id = request.form.get('patient_id')
    to_doctor_id = request.form.get('to_doctor_id') or None
    to_specialty = request.form.get('to_specialty') or None
    reason = request.form.get('reason')
    if not patient_id or not reason:
        flash('Patient and reason are required.', 'warning')
        return redirect(url_for('care.referrals'))
    p = Patient.query.filter_by(id=int(patient_id)).first()
    if p is None:
        flash('Patient not found.', 'warning')
        return redirect(url_for('care.referrals'))
    require_patient_access(p)
    ref = Referral(
        patient_id=p.id,
        from_doctor_id=doc.id if doc else None,
        to_doctor_id=int(to_doctor_id) if to_doctor_id else None,
        to_specialty=to_specialty, reason=reason, status='Pending',
    )
    db.session.add(ref)
    db.session.flush()
    log_activity('CREATE_REFERRAL', 'referral', ref.id,
                 f'patient_id={patient_id} to={to_specialty or to_doctor_id}')
    db.session.commit()
    flash('Referral created.', 'success')
    return redirect(url_for('care.referrals'))


@care_bp.route('/referrals/<int:ref_id>/status', methods=['POST'])
@login_required
@roles_required(*CARE)
def update_referral_status(ref_id):
    ref = Referral.query.get_or_404(ref_id)
    require_patient_access(ref.patient)
    new_status = request.form.get('status')
    if new_status in ('Pending', 'Accepted', 'Rejected', 'Completed'):
        ref.status = new_status
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
    cases_list = MultidisciplinaryCase.query\
        .order_by(MultidisciplinaryCase.created_at.desc()).all()
    return render_template('care/cases.html', title='Multidisciplinary Cases',
                           cases=cases_list, patients=Patient.query.all())


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
