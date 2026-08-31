from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime, date
from app import db
from app.models import (
    PhysicalTherapist, TherapyAssessment, TherapyPlan, TherapySession,
    TherapyExercise, ExerciseLibraryItem, RehabilitationProgress,
    FunctionalOutcome, Patient, User,
)
from app.routes.decorators import roles_required, log_activity
from app.access import patient_access_required, require_patient_access

physiotherapy_bp = Blueprint('physiotherapy', __name__)


def get_current_therapist():
    return PhysicalTherapist.query.filter_by(user_id=current_user.id).first()


@physiotherapy_bp.route('/dashboard')
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
def dashboard():
    today = date.today()
    today_sessions_count = TherapySession.query.filter(
        db.func.date(TherapySession.scheduled_at) == today
    ).count()
    active_plans_count = TherapyPlan.query.filter_by(status='Active').count()
    high_risk_count = TherapyAssessment.query.filter(
        TherapyAssessment.pain_assessment >= 7
    ).count()
    recent_sessions = TherapySession.query.order_by(
        TherapySession.scheduled_at.desc()
    ).limit(8).all()
    recent_plans = TherapyPlan.query.order_by(
        TherapyPlan.id.desc()
    ).limit(8).all()
    return render_template(
        'physiotherapy/dashboard.html',
        today_sessions_count=today_sessions_count,
        active_plans_count=active_plans_count,
        high_risk_count=high_risk_count,
        recent_sessions=recent_sessions,
        recent_plans=recent_plans,
        today=today,
    )


@physiotherapy_bp.route('/patients')
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
def patients():
    search = request.args.get('q', '').strip()
    query = Patient.query.join(User)
    if search:
        query = query.filter(
            db.or_(
                User.full_name.ilike(f'%{search}%'),
                User.email.ilike(f'%{search}%'),
            )
        )
    patients_list = query.order_by(User.full_name).all()
    return render_template(
        'physiotherapy/patients.html',
        patients=patients_list,
        search=search,
    )


@physiotherapy_bp.route('/patients/<int:patient_id>/assessment',
                        methods=['GET', 'POST'])
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
@patient_access_required
def assessment(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    therapist = get_current_therapist()
    if request.method == 'POST':
        assessment = TherapyAssessment(
            patient_id=patient_id,
            therapist_id=therapist.id if therapist else None,
            functional_assessment=request.form.get('functional_assessment', ''),
            mobility_assessment=request.form.get('mobility_assessment', ''),
            pain_assessment=int(request.form.get('pain_assessment', 0)),
            muscle_strength=request.form.get('muscle_strength', ''),
            balance_assessment=request.form.get('balance_assessment', ''),
            range_of_motion=request.form.get('range_of_motion', ''),
            posture_evaluation=request.form.get('posture_evaluation', ''),
            gait_analysis=request.form.get('gait_analysis', ''),
            notes=request.form.get('notes', ''),
        )
        db.session.add(assessment)
        db.session.flush()
        log_activity('CREATE_THERAPY_ASSESSMENT', 'therapy_assessment',
                      assessment.id, f'patient_id={patient_id}')
        db.session.commit()
        flash('Assessment saved successfully.', 'success')
        return redirect(url_for('physiotherapy.assessment', patient_id=patient_id))

    assessments = TherapyAssessment.query.filter_by(
        patient_id=patient_id
    ).order_by(TherapyAssessment.assessed_at.desc()).all()
    return render_template(
        'physiotherapy/assessment.html',
        patient=patient,
        assessments=assessments,
    )


@physiotherapy_bp.route('/patients/<int:patient_id>/plan',
                        methods=['GET', 'POST'])
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
@patient_access_required
def plan(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    therapist = get_current_therapist()
    if request.method == 'POST':
        start_date = end_date = None
        try:
            if request.form.get('start_date'):
                start_date = datetime.strptime(
                    request.form['start_date'], '%Y-%m-%d').date()
            if request.form.get('end_date'):
                end_date = datetime.strptime(
                    request.form['end_date'], '%Y-%m-%d').date()
        except ValueError:
            flash('Please provide valid dates (YYYY-MM-DD).', 'danger')
            return redirect(url_for('physiotherapy.plan', patient_id=patient_id))
        plan = TherapyPlan(
            patient_id=patient_id,
            therapist_id=therapist.id if therapist else None,
            title=request.form.get('title', ''),
            goals=request.form.get('goals', ''),
            objectives=request.form.get('objectives', ''),
            interventions=request.form.get('interventions', ''),
            start_date=start_date or date.today(),
            end_date=end_date,
            status=request.form.get('status', 'Active'),
        )
        db.session.add(plan)
        db.session.flush()
        log_activity('CREATE_THERAPY_PLAN', 'therapy_plan', plan.id,
                      f'patient_id={patient_id} title={plan.title}')
        db.session.commit()
        flash('Treatment plan created successfully.', 'success')
        return redirect(url_for('physiotherapy.plan', patient_id=patient_id))

    plans = TherapyPlan.query.filter_by(patient_id=patient_id).order_by(
        TherapyPlan.id.desc()
    ).all()
    return render_template(
        'physiotherapy/plan.html',
        patient=patient,
        plans=plans,
        today=date.today().strftime('%Y-%m-%d'),
    )


@physiotherapy_bp.route('/plans/<int:plan_id>/session',
                        methods=['GET', 'POST'])
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
def session(plan_id):
    plan = TherapyPlan.query.get_or_404(plan_id)
    require_patient_access(plan.patient)
    therapist = get_current_therapist()
    if request.method == 'POST':
        scheduled_at = None
        if request.form.get('scheduled_at'):
            try:
                scheduled_at = datetime.strptime(
                    request.form['scheduled_at'], '%Y-%m-%dT%H:%M')
            except ValueError:
                flash('Please provide a valid date and time.', 'danger')
                return redirect(url_for('physiotherapy.session', plan_id=plan_id))
        session = TherapySession(
            patient_id=plan.patient_id,
            therapist_id=therapist.id if therapist else None,
            plan_id=plan_id,
            session_type=request.form.get('session_type', 'Individual'),
            scheduled_at=scheduled_at,
            duration_minutes=int(request.form.get('duration_minutes', 45)),
            status=request.form.get('status', 'Scheduled'),
            notes=request.form.get('notes', ''),
        )
        db.session.add(session)
        db.session.flush()
        log_activity('CREATE_THERAPY_SESSION', 'therapy_session', session.id,
                      f'plan_id={plan_id} patient_id={plan.patient_id}')
        db.session.commit()
        flash('Session scheduled successfully.', 'success')
        return redirect(url_for('physiotherapy.session', plan_id=plan_id))

    sessions = TherapySession.query.filter_by(plan_id=plan_id).order_by(
        TherapySession.scheduled_at.desc()
    ).all()
    return render_template(
        'physiotherapy/sessions.html',
        plan=plan,
        sessions=sessions,
    )


@physiotherapy_bp.route('/patients/<int:patient_id>/progress',
                        methods=['GET', 'POST'])
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
@patient_access_required
def progress(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if request.method == 'POST':
        progress = RehabilitationProgress(
            patient_id=patient_id,
            plan_id=int(request.form['plan_id']) if request.form.get('plan_id') else None,
            session_id=int(request.form['session_id']) if request.form.get('session_id') else None,
            pain_score=int(request.form.get('pain_score', 0)),
            mobility_score=int(request.form.get('mobility_score', 0)),
            strength_score=int(request.form.get('strength_score', 0)),
            functional_outcome=int(request.form.get('functional_outcome', 0)),
            range_of_motion=request.form.get('range_of_motion', ''),
            balance_score=int(request.form.get('balance_score', 0)),
            compliance=int(request.form.get('compliance', 0)),
            notes=request.form.get('notes', ''),
        )
        db.session.add(progress)
        db.session.flush()
        log_activity('CREATE_REHAB_PROGRESS', 'rehabilitation_progress', progress.id,
                      f'patient_id={patient_id}')
        db.session.commit()
        flash('Progress recorded successfully.', 'success')
        return redirect(url_for('physiotherapy.progress', patient_id=patient_id))

    progress_entries = RehabilitationProgress.query.filter_by(
        patient_id=patient_id
    ).order_by(RehabilitationProgress.recorded_at.desc()).all()
    plans = TherapyPlan.query.filter_by(patient_id=patient_id).all()
    sessions = TherapySession.query.filter_by(patient_id=patient_id).all()
    return render_template(
        'physiotherapy/progress.html',
        patient=patient,
        progress_entries=progress_entries,
        plans=plans,
        sessions=sessions,
    )


@physiotherapy_bp.route('/exercise-library')
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
def exercise_library():
    category = request.args.get('category', '').strip()
    query = ExerciseLibraryItem.query
    if category:
        query = query.filter_by(category=category)
    items = query.order_by(ExerciseLibraryItem.name).all()
    categories = [
        c[0] for c in db.session.query(
            ExerciseLibraryItem.category
        ).distinct().all() if c[0]
    ]
    return render_template(
        'physiotherapy/exercise_library.html',
        items=items,
        categories=categories,
        current_category=category,
    )


@physiotherapy_bp.route('/exercise-library/add',
                        methods=['GET', 'POST'])
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
def add_exercise():
    if request.method == 'POST':
        item = ExerciseLibraryItem(
            name=request.form['name'],
            description=request.form.get('description', ''),
            instructions=request.form.get('instructions', ''),
            image_url=request.form.get('image_url', ''),
            video_url=request.form.get('video_url', ''),
            repetitions=int(request.form['repetitions']) if request.form.get('repetitions') else None,
            duration_seconds=int(request.form['duration_seconds']) if request.form.get('duration_seconds') else None,
            progression_plan=request.form.get('progression_plan', ''),
            category=request.form.get('category', ''),
        )
        db.session.add(item)
        db.session.flush()
        log_activity('ADD_EXERCISE_LIBRARY_ITEM', 'exercise_library', item.id,
                      f'name={item.name}')
        db.session.commit()
        flash('Exercise added to library successfully.', 'success')
        return redirect(url_for('physiotherapy.exercise_library'))

    return render_template('physiotherapy/add_exercise.html')


@physiotherapy_bp.route('/sessions/<int:session_id>/start', methods=['POST'])
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
def session_start(session_id):
    session = TherapySession.query.get_or_404(session_id)
    require_patient_access(session.patient)
    if session.status not in ('Scheduled',):
        flash('Only a scheduled session can be started.', 'warning')
        return redirect(url_for('physiotherapy.plan', plan_id=session.plan_id))
    session.status = 'In Progress'
    session.started_at = datetime.now()
    session.pain_before = request.form.get('pain_before', type=int)
    log_activity('START_THERAPY_SESSION', 'therapy_session', session.id,
                 f'patient_id={session.patient_id}')
    db.session.commit()
    flash('Session started.', 'success')
    return redirect(url_for('physiotherapy.plan', plan_id=session.plan_id))


@physiotherapy_bp.route('/sessions/<int:session_id>/complete', methods=['POST'])
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
def session_complete(session_id):
    session = TherapySession.query.get_or_404(session_id)
    require_patient_access(session.patient)
    if session.status not in ('In Progress', 'Scheduled'):
        flash('Only an in-progress session can be completed.', 'warning')
        return redirect(url_for('physiotherapy.plan', plan_id=session.plan_id))
    session.status = 'Completed'
    session.settled_at = datetime.now()
    session.pain_after = request.form.get('pain_after', type=int)
    session.exercises_performed = request.form.get('exercises_performed')
    session.modalities = request.form.get('modalities')
    session.patient_response = request.form.get('patient_response')
    session.followup_required = bool(request.form.get('followup_required'))
    session.adherence = request.form.get('adherence')
    session.notes = request.form.get('notes') or session.notes
    if not session.started_at:
        session.started_at = datetime.now()
    log_activity('COMPLETE_THERAPY_SESSION', 'therapy_session', session.id,
                 f'patient_id={session.patient_id}')
    from app.services.billing import ensure_bill_for_physio
    ensure_bill_for_physio(session.id)
    from app.services.notifications import notify_patient
    notify_patient(session.patient, 'Physiotherapy session completed',
                   f'Session #{session.id} completed for your treatment plan.',
                   entity_type='therapy_session', entity_id=session.id)
    db.session.commit()
    flash('Session completed and billed.', 'success')
    return redirect(url_for('physiotherapy.plan', plan_id=session.plan_id))


@physiotherapy_bp.route('/sessions/<int:session_id>/cancel', methods=['POST'])
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
def session_cancel(session_id):
    session = TherapySession.query.get_or_404(session_id)
    require_patient_access(session.patient)
    session.status = 'Cancelled'
    log_activity('CANCEL_THERAPY_SESSION', 'therapy_session', session.id,
                 f'patient_id={session.patient_id}')
    db.session.commit()
    flash('Session cancelled.', 'success')
    return redirect(url_for('physiotherapy.plan', plan_id=session.plan_id))


@physiotherapy_bp.route('/sessions/<int:session_id>/no-show', methods=['POST'])
@login_required
@roles_required('Physiotherapist', 'Admin', 'SuperAdmin')
def session_no_show(session_id):
    session = TherapySession.query.get_or_404(session_id)
    require_patient_access(session.patient)
    session.status = 'No Show'
    log_activity('NO_SHOW_THERAPY_SESSION', 'therapy_session', session.id,
                 f'patient_id={session.patient_id}')
    db.session.commit()
    flash('Session marked as no show.', 'info')
    return redirect(url_for('physiotherapy.plan', plan_id=session.plan_id))
