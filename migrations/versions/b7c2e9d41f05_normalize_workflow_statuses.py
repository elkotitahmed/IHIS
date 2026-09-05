"""normalize legacy workflow status values

Older seed scripts and imports wrote upper-case or non-canonical status
values ('ORDERED', 'PERFORMED', 'FINALIZED', 'Completed', 'In Progress',
'No Show', 'Accepted', 'Rejected', ...). The application's state machines
(app/services/status.py, app/services/clinical_orders.py) only recognise
the canonical spellings, so those rows were stuck with no available actions.
This data migration maps every legacy value onto its canonical equivalent.
It is idempotent and does not touch rows that are already canonical.

Revision ID: b7c2e9d41f05
Revises: 8f3c0d1a2e9b
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa

revision = 'b7c2e9d41f05'
down_revision = '8f3c0d1a2e9b'
branch_labels = None
depends_on = None


LAB_ORDER = {
    'ORDERED': 'Pending', 'Ordered': 'Pending', 'PENDING': 'Pending',
    'ACCEPTED': 'Accepted', 'COLLECTED': 'Collected', 'PROCESSING': 'Processing',
    'RESULTED': 'Resulted', 'VERIFIED': 'Verified', 'FINALIZED': 'Finalized',
    'Completed': 'Finalized', 'COMPLETED': 'Finalized', 'REJECTED': 'Rejected',
    'CANCELLED': 'Cancelled',
}
RADIOLOGY_ORDER = {
    'ORDERED': 'Pending', 'Ordered': 'Pending', 'PENDING': 'Pending',
    'SCHEDULED': 'Scheduled', 'ARRIVED': 'Arrived', 'IN_PROGRESS': 'InProgress',
    'In Progress': 'InProgress', 'PERFORMED': 'Performed', 'REPORTED': 'Reported',
    'SIGNED': 'Signed', 'FINALIZED': 'Finalized', 'Completed': 'Signed',
    'COMPLETED': 'Signed', 'CANCELLED': 'Cancelled',
}
THERAPY_SESSION = {
    'In Progress': 'InProgress', 'No Show': 'NoShow', 'Checked In': 'CheckedIn',
}
REFERRAL = {
    'Sent': 'SENT', 'Accepted': 'ACCEPTED', 'Rejected': 'REJECTED',
    'Completed': 'COMPLETED', 'Closed': 'CLOSED', 'In Review': 'IN_REVIEW',
}
LAB_RESULT = {'FINALIZED': 'Finalized', 'VERIFIED': 'Verified', 'DRAFT': 'Draft'}
RADIOLOGY_REPORT = {'SIGNED': 'Signed', 'DRAFT': 'Draft', 'FINALIZED': 'Finalized'}


def _apply(table, mapping):
    for old, new in mapping.items():
        if old == new:
            continue
        op.execute(sa.text(f"UPDATE {table} SET status = :new WHERE status = :old")
                   .bindparams(new=new, old=old))


def upgrade():
    _apply('lab_orders', LAB_ORDER)
    _apply('radiology_orders', RADIOLOGY_ORDER)
    _apply('therapy_sessions', THERAPY_SESSION)
    _apply('referrals', REFERRAL)
    _apply('lab_results', LAB_RESULT)
    _apply('radiology_reports', RADIOLOGY_REPORT)


def downgrade():
    # Canonical values are a superset of what older code accepted; there is
    # nothing safe to reverse here (the legacy spellings were never valid
    # workflow states), so the downgrade is a no-op by design.
    pass
