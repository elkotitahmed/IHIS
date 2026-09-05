"""Global task / work-queue engine.

One reusable task engine with department-specific task types. Any staff user
can open their task list; admins/supervisors see department queues.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, abort, request

from flask_login import login_required, current_user

from app import db
from app.models import Task, TaskActivity, Patient, Notification
from app.routes.decorators import log_activity
from app.services import tasks as task_svc

tasks_bp = Blueprint('tasks', __name__)


TASK_ACTIONS = {
    'ASSIGNED': ('Warning', 'Assign'),
    'IN_PROGRESS': ('primary', 'Start'),
    'COMPLETED': ('success', 'Complete'),
    'ON_HOLD': ('secondary', 'Hold'),
    'REJECTED': ('danger', 'Reject'),
}

# staff roles that may view the queue (not patients)
STAFF_ROLES = ('SuperAdmin', 'Admin', 'Doctor', 'Nurse', 'LabTechnician',
               'Radiologist', 'RadiologyTechnician', 'Pharmacist', 'Receptionist',
               'Dentist', 'Physiotherapist', 'Cashier')


def _staff_only():
    if not any(current_user.has_role(r) for r in STAFF_ROLES):
        abort(403)


@tasks_bp.route('/my-tasks')
@login_required
def my_tasks():
    _staff_only()
    tasks = task_svc.my_tasks(current_user)
    completed = Task.query.filter_by(assigned_to=current_user.id,
                                     status='COMPLETED').order_by(
        Task.completed_at.desc()).limit(20).all()
    return render_template('tasks/my_tasks.html', title='My Tasks',
                           tasks=tasks, completed=completed,
                           actions=TASK_ACTIONS)


@tasks_bp.route('/queue')
@login_required
def queue():
    _staff_only()
    dept = request.args.get('department', '').strip()
    status = request.args.get('status', '').strip()
    q = Task.query
    # Restrict what a non-admin sees to tasks relevant to their own department.
    role_names = [r.name for r in current_user.roles]
    if not current_user.has_any_role('Admin', 'SuperAdmin'):
        # A staff member sees work addressed to them, to their role, or to
        # their own department - never other departments' queues.
        own_dept = current_user.department.name if current_user.department else None
        scope = [Task.assigned_to == current_user.id,
                 Task.assigned_role.in_(role_names)]
        if own_dept:
            scope.append(Task.department == own_dept)
        q = q.filter(db.or_(*scope))
        if dept:
            q = q.filter(Task.department == dept)
    else:
        if dept:
            q = q.filter(Task.department == dept)
    if status:
        q = q.filter(Task.status == status)
    else:
        q = q.filter(Task.status.in_(('NEW', 'ASSIGNED', 'IN_PROGRESS', 'ON_HOLD', 'REJECTED')))
    tasks = q.order_by(Task.priority, Task.created_at.asc()).limit(300).all()
    departments = task_svc.distinct_departments()
    return render_template('tasks/queue.html', title='Department Queue',
                           tasks=tasks, departments=departments,
                           current_department=dept, current_status=status,
                           actions=TASK_ACTIONS)


@tasks_bp.route('/tasks/<int:task_id>')
@login_required
def detail(task_id):
    _staff_only()
    task = db.session.get(Task, task_id) or abort(404)
    own_dept = current_user.department.name if current_user.department else None
    if not current_user.has_any_role('Admin', 'SuperAdmin') and \
       task.assigned_to != current_user.id and \
       task.assigned_role not in (r.name for r in current_user.roles) and \
       not (own_dept and task.department == own_dept):
        abort(403)
    activities = TaskActivity.query.filter_by(task_id=task.id).order_by(
        TaskActivity.created_at.desc()).all()
    return render_template('tasks/detail.html', title=task.title, task=task,
                           activities=activities, actions=TASK_ACTIONS)


@tasks_bp.route('/tasks/<int:task_id>/assign', methods=['POST'])
@login_required
def assign(task_id):
    _staff_only()
    task = db.session.get(Task, task_id) or abort(404)
    if not current_user.has_any_role('Admin', 'SuperAdmin'):
        abort(403)
    user_id = request.form.get('assignee_id', type=int)
    if user_id:
        task.assigned_to = user_id
        task.assigned_role = None
    _do_transition(task, 'ASSIGNED', request.form.get('note'),
                   allow_same=True)
    db.session.commit()
    log_activity('ASSIGN_TASK', 'task', task.id)
    flash('Task reassigned.', 'success')
    return redirect(url_for('tasks.detail', task_id=task.id))


@tasks_bp.route('/tasks/<int:task_id>/transition', methods=['POST'])
@login_required
def transition(task_id):
    _staff_only()
    task = db.session.get(Task, task_id) or abort(404)
    role_names = [r.name for r in current_user.roles]
    own_dept = current_user.department.name if current_user.department else None
    if not current_user.has_any_role('Admin', 'SuperAdmin'):
        if task.assigned_to is not None and task.assigned_to != current_user.id:
            abort(403)
        if task.assigned_to is None and task.assigned_role not in role_names \
                and not (own_dept and task.department == own_dept):
            abort(403)
    to_status = request.form.get('status', '').upper()
    # Claiming an unassigned task: whoever starts it owns it.
    if task.assigned_to is None and to_status in ('IN_PROGRESS', 'ASSIGNED'):
        task.assigned_to = current_user.id
    note = request.form.get('note')
    try:
        _do_transition(task, to_status, note)
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
        return redirect(url_for('tasks.detail', task_id=task.id))
    db.session.commit()
    log_activity(f'TASK_{to_status}', 'task', task.id)
    task_svc.notify_task_activity(task)
    db.session.commit()
    flash(f'Task marked {to_status}.', 'success')
    return redirect(url_for('tasks.detail', task_id=task.id))


def _do_transition(task, to_status, note=None, allow_same=False):
    from app.services import tasks as svc
    if allow_same and task.status == to_status:
        return
    svc.transition(task, to_status, note)


@tasks_bp.route('/task/new', methods=['GET', 'POST'])
@login_required
def new_task():
    _staff_only()
    if request.method == 'POST':
        patient_id = request.form.get('patient_id', type=int)
        title = request.form.get('title', '').strip()
        if not title:
            flash('Task title is required.', 'danger')
            return redirect(url_for('tasks.new_task'))
        due = request.form.get('due_at')
        due_at = None
        if due:
            from datetime import datetime
            try:
                due_at = datetime.strptime(due, '%Y-%m-%dT%H:%M')
            except ValueError:
                due_at = None
        assignee_id = request.form.get('assignee_id', type=int)
        if patient_id:
            from app.access import require_patient_access
            require_patient_access(db.session.get(Patient, patient_id))
        task = task_svc.create_task(
            title=title,
            description=request.form.get('description'),
            task_type=request.form.get('task_type') or 'GENERAL',
            department=request.form.get('department'),
            patient_id=patient_id,
            assigned_to=assignee_id,
            assigned_role=request.form.get('assigned_role'),
            priority=request.form.get('priority') or 'Normal',
            due_at=due_at,
        )
        db.session.commit()
        log_activity('CREATE_TASK', 'task', task.id)
        task_svc.notify_task_activity(task)
        db.session.commit()
        flash('Task created.', 'success')
        return redirect(url_for('tasks.detail', task_id=task.id))
    from app.access import accessible_patient_ids
    from app.models import User
    pids = accessible_patient_ids(current_user)
    patients = (Patient.query.join(User, Patient.user_id == User.id)
                .filter(Patient.id.in_(sorted(pids) if pids else [-1]))
                .order_by(User.full_name).limit(300).all())
    staff = (User.query.filter(User.user_type != 'patient', User.is_active.is_(True))
             .order_by(User.full_name).all())
    return render_template('tasks/new_task.html', title='New Task',
                           patients=patients, staff=staff,
                           task_types=task_svc.TASK_TYPES)
