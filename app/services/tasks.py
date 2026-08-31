"""Reusable task/work-item engine shared across all departments.

A single ``Task`` model backs every department's work queue. Departments
create typed tasks and route them to the staff roles that own them. Each
transition writes a ``TaskActivity`` audit row so the full history of a
task is reconstructable.
"""
from flask_login import current_user

from app import db
from app.models import Task, TaskActivity
from app.utils import utcnow


# ---- Task statuses / priorities / valid transitions (state machine) ----
TASK_STATUSES = ('NEW', 'ASSIGNED', 'IN_PROGRESS', 'ON_HOLD', 'COMPLETED',
                 'CANCELLED', 'REJECTED')
TASK_PRIORITIES = ('LOW', 'NORMAL', 'HIGH', 'URGENT', 'CRITICAL')

# TEMPLATE restricts task-type -> department/task assignments to a whitelist.
TASK_TYPES = ('LAB', 'RADIOLOGY', 'PHARMACY', 'NURSING', 'REFERRAL',
              'PHYSIOTHERAPY', 'DENTISTRY', 'DOCUMENT_REVIEW',
              'RESULT_VERIFICATION', 'ADMISSION', 'GENERAL')

_VALID_TRANSITIONS = {
    'NEW': {'ASSIGNED', 'IN_PROGRESS', 'CANCELLED'},
    'ASSIGNED': {'IN_PROGRESS', 'ON_HOLD', 'REJECTED', 'NEW', 'CANCELLED'},
    'IN_PROGRESS': {'ASSIGNED', 'ON_HOLD', 'COMPLETED', 'CANCELLED'},
    'ON_HOLD': {'ASSIGNED', 'IN_PROGRESS', 'CANCELLED'},
    'COMPLETED': set(),
    'CANCELLED': set(),
    'REJECTED': {'NEW', 'ASSIGNED', 'IN_PROGRESS'},
}


def allowed_transition(current_status, new_status):
    """True when ``new_status`` is a permitted next state for ``current``."""
    if current_status == new_status:
        return True
    return new_status in _VALID_TRANSITIONS.get(current_status, set())


def _uid():
    return current_user.id if current_user and current_user.is_authenticated else None


def my_tasks(user=None, statuses=None):
    """Tasks assigned to the current user (or ``user``), optionally filtered
    by status list (default: open statuses only)."""
    user = user or current_user
    q = Task.query.filter(Task.assigned_to == user.id)
    if statuses:
        q = q.filter(Task.status.in_(statuses))
    else:
        q = q.filter(Task.status.in_(('NEW', 'ASSIGNED', 'IN_PROGRESS', 'ON_HOLD')))
    return q.order_by(Task.priority.desc(), Task.due_at.asc().nulls_last()).all()


def department_queue(department, statuses=None):
    """Tasks belonging to a department queue (optionally filtered by status)."""
    q = Task.query.filter(Task.department == department)
    if statuses:
        q = q.filter(Task.status.in_(statuses))
    return q.order_by(Task.priority.desc(), Task.created_at.asc()).all()


def create_task(title, description=None, task_type='GENERAL', department=None,
                patient_id=None, assigned_to=None, assigned_role=None,
                priority='Normal', due_at=None, related_resource_type=None,
                related_resource_id=None):
    """Create a new task (default status NEW) and write a CREATED activity."""
    task = Task(
        title=title, description=description, task_type=task_type,
        department=department, patient_id=patient_id,
        created_by=_uid(), assigned_to=assigned_to, assigned_role=assigned_role,
        priority=priority, due_at=due_at,
        related_resource_type=related_resource_type,
        related_resource_id=related_resource_id,
        status='NEW',
    )
    db.session.add(task)
    db.session.flush()
    db.session.add(TaskActivity(task_id=task.id, user_id=_uid(), action='CREATED',
                                from_status=None, to_status='NEW'))
    return task


def transition(task, new_status, note=None):
    """Apply a validated status transition with a full audit trail.

    Raises ValueError on an illegal transition so callers can surface a
    controlled error instead of letting clients jump arbitrarily between
    states.
    """
    if new_status not in TASK_STATUSES:
        raise ValueError(f'Unknown task status: {new_status}')
    if not allowed_transition(task.status, new_status):
        raise ValueError(
            f'Cannot move task from {task.status} to {new_status}')
    from_status = task.status
    task.status = new_status
    if new_status == 'IN_PROGRESS' and not task.started_at:
        task.started_at = utcnow()
    if new_status == 'COMPLETED':
        task.completed_at = utcnow()
    task.updated_at = utcnow()
    db.session.add(TaskActivity(task_id=task.id, user_id=_uid(), action='TRANSITION',
                                from_status=from_status, to_status=new_status,
                                note=note))
    return task


def notify_task_activity(task):
    """Create a notification for the task assignee (if set) when a task is
    created/assigned, linking back to the task resource."""
    from app.services.notifications import notify
    if task.assigned_to:
        notify(task.assigned_to, f'Task assigned: {task.title}',
               f'A task has been assigned to you ({task.status}).',
               entity_type='task', entity_id=task.id)
