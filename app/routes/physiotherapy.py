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
        log_activity('CREATE_THERAPY_ASSESSMENT', 'therapy_assessment',
                      None, f'patient_id={patient_id}')
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
def plan(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    therapist = get_current_therapist()
    if request.method == 'POST':
        plan = TherapyPlan(
            patient_id=patient_id,
            therapist_id=therapist.id if therapist else None,
            title=request.form.get('title', ''),
            goals=request.form.get('goals', ''),
            objectives=request.form.get('objectives', ''),
            interventions=request.form.get('interventions', ''),
            start_date=datetime.strptime(
                request.form['start_date'], '%Y-%m-%d'
            ).date() if request.form.get('start_date') else date.today(),
            end_date=datetime.strptime(
                request.form['end_date'], '%Y-%m-%d'
            ).date() if request.form.get('end_date') else None,
            status=request.form.get('status', 'Active'),
        )
        db.session.add(plan)
        log_activity('CREATE_THERAPY_PLAN', 'therapy_plan', None,
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
    therapist = get_current_therapist()
    if request.method == 'POST':
        scheduled_at = datetime.strptime(
            request.form['scheduled_at'], '%Y-%m-%dT%H:%M'
        ) if request.form.get('scheduled_at') else None
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
        log_activity('CREATE_THERAPY_SESSION', 'therapy_session', None,
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
        log_activity('CREATE_REHAB_PROGRESS', 'rehabilitation_progress', None,
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
        log_activity('ADD_EXERCISE_LIBRARY_ITEM', 'exercise_library', None,
                      f'name={item.name}')
        db.session.commit()
        flash('Exercise added to library successfully.', 'success')
        return redirect(url_for('physiotherapy.exercise_library'))

    return render_template('physiotherapy/add_exercise.html')
