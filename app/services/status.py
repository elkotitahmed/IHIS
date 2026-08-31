"""Explicit workflow state machines for clinical records.

Instead of letting clients arbitrarily set a record's status, every clinical
record type has a whitelist of legal transitions. Attempting an illegal
transition raises ``StatusTransitionError`` which routes convert into a
controlled message rather than silently accepting a bad state.
"""
from datetime import datetime


class StatusTransitionError(Exception):
    """Raised when a record is asked to move to an illegal next state."""


# Each map: current_status -> set(legal_next_statuses)
WORKFLOWS = {
    'lab_order': {
        'Pending': {'Accepted', 'Collected', 'Rejected'},
        'Accepted': {'Collected', 'Processing', 'Cancelled'},
        'Collected': {'ReceivedAtLab', 'Processing', 'Rejected'},
        'ReceivedAtLab': {'Processing', 'Rejected'},
        'Processing': {'Resulted', 'Rejected'},
        'Resulted': {'Verified', 'Finalized'},
        'Verified': {'Finalized'},
        'Finalized': set(),
        'Rejected': {'Reordered'},
        'Reordered': {'Accepted', 'Processing', 'Cancelled'},
        'Cancelled': set(),
    },
    'radiology_order': {
        'Pending': {'Scheduled', 'Cancelled'},
        'Scheduled': {'Arrived', 'Cancelled'},
        'Arrived': {'InProgress', 'Performed', 'Cancelled'},
        'InProgress': {'Performed', 'Cancelled'},
        'Performed': {'Reported', 'Cancelled'},
        'Reported': {'Signed', 'Finalized'},
        'Signed': {'Finalized'},
        'Finalized': set(),
        'Cancelled': set(),
    },
    'lab_result': {
        'Draft': {'Verified', 'Locked'},
        'Verified': {'Locked', 'Draft'},   # Draft = reopened for amendment
        'Locked': set(),
    },
    'radiology_report': {
        'Draft': {'Signed', 'Locked'},
        'Signed': {'Locked', 'Draft'},
        'Locked': set(),
    },
    'medical_record': {
        'Draft': {'Signed', 'Finalized', 'Locked'},
        'Signed': {'Finalized', 'Locked', 'Draft'},
        'Finalized': {'Locked', 'Draft'},
        'Locked': set(),
    },
    'prescription': {
        'Draft': {'Signed', 'Active', 'Cancelled'},
        'Signed': {'Active', 'Cancelled'},
        'Active': {'Dispensed', 'PartiallyDispensed', 'Cancelled', 'Completed'},
        'PartiallyDispensed': {'Dispensed', 'Completed', 'Cancelled'},
        'Dispensed': {'Completed'},
        'Completed': {'Dispensed', 'Cancelled'},
        'Cancelled': set(),
    },
    'prescription_item': {
        'Active': {'Dispensed', 'Cancelled'},
        'PartiallyDispensed': {'Dispensed'},
        'Dispensed': set(),
        'Cancelled': set(),
    },
    'appointment': {
        'Scheduled': {'Confirmed', 'CheckedIn', 'Cancelled', 'NoShow'},
        'Confirmed': {'Scheduled', 'CheckedIn', 'Cancelled', 'NoShow'},
        'CheckedIn': {'InConsultation', 'Completed', 'Cancelled', 'NoShow'},
        'InConsultation': {'Completed', 'CheckedIn', 'NoShow'},
        'Completed': {'Cancelled'},
        'NoShow': set(),
        'Cancelled': set(),
    },
    'admission': {
        'Admitted': {'Moved', 'Discharged'},
        'Moved': {'Admitted', 'Discharged'},
        'Discharged': set(),
    },
    'therapy_session': {
        'Scheduled': {'CheckedIn', 'InProgress', 'NoShow', 'Cancelled'},
        'CheckedIn': {'InProgress', 'Completed', 'Cancelled'},
        'InProgress': {'Completed', 'Followup', 'NoShow'},
        'Followup': {'Scheduled', 'Completed'},
        'Completed': {'Followup'},
        'NoShow': {'Scheduled', 'Cancelled', 'Completed'},
        'Cancelled': set(),
    },
    'dental_procedure': {
        'Planned': {'Scheduled', 'InProgress', 'Completed', 'Cancelled'},
        'Scheduled': {'InProgress', 'Completed', 'Cancelled', 'Planned'},
        'InProgress': {'Completed', 'Scheduled', 'Cancelled'},
        'Completed': set(),
        'Cancelled': set(),
    },
    'dental_treatment_plan': {
        'Planned': {'Scheduled', 'InProgress', 'Completed', 'Cancelled'},
        'Scheduled': {'InProgress', 'Completed', 'Cancelled'},
        'InProgress': {'Completed', 'Cancelled'},
        'Completed': set(),
        'Cancelled': set(),
    },
    'medication_administration': {
        'Scheduled': {'Due', 'Administered', 'Held', 'Cancelled'},
        'Due': {'Administered', 'Refused', 'Held', 'Missed'},
        'Administered': set(),
        'Refused': set(),
        'Held': {'Administered', 'Due', 'Missed'},
        'Missed': {'Administered'},
        'Cancelled': set(),
    },
    'task': {
        'NEW': {'ASSIGNED', 'IN_PROGRESS', 'CANCELLED'},
        'ASSIGNED': {'IN_PROGRESS', 'ON_HOLD', 'REJECTED', 'NEW', 'CANCELLED'},
        'IN_PROGRESS': {'ASSIGNED', 'ON_HOLD', 'COMPLETED', 'CANCELLED'},
        'ON_HOLD': {'ASSIGNED', 'IN_PROGRESS', 'CANCELLED'},
        'COMPLETED': set(),
        'CANCELLED': set(),
        'REJECTED': {'NEW', 'ASSIGNED', 'IN_PROGRESS'},
    },
}


# The statuses that, once reached for each record type, lock the record
# against further editing by any ordinary route.
LOCKED_STATUSES = {
    'lab_result': {'Verified', 'Locked', 'Finalized'},
    'radiology_report': {'Signed', 'Locked', 'Finalized'},
    'medical_record': {'Signed', 'Finalized', 'Locked'},
}


def assert_transition(workflow, record, new_status):
    """Check ``record`` may move to ``new_status``; raise on violation."""
    legal = WORKFLOWS.get(workflow, {})
    current = record.status or ''
    if new_status not in legal.get(current, set()):
        raise StatusTransitionError(
            f'Illegal transition: {workflow} {current} -> {new_status}')
    return True


def is_terminal(workflow, status):
    return status in WORKFLOWS.get(workflow, {}) and \
        not WORKFLOWS[workflow].get(status, set())


def is_locked(workflow, status):
    return status in LOCKED_STATUSES.get(workflow, set())
