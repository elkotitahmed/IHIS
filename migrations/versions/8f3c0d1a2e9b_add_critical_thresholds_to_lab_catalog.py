"""add critical value thresholds to the lab test catalog

Revision ID: 8f3c0d1a2e9b
Revises: f1b1a6907bef
Create Date: 2026-09-05 17:40:00.000000

Adds panic-value (critical) thresholds to LabTestCatalog. A numeric lab result
below critical_low or above critical_high is now auto-flagged is_critical and
escalated to ordering doctors.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8f3c0d1a2e9b'
down_revision = 'f1b1a6907bef'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('lab_test_catalog') as batch_op:
        batch_op.add_column(sa.Column('critical_low', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('critical_high', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('critical_notes', sa.String(length=250), nullable=True))


def downgrade():
    with op.batch_alter_table('lab_test_catalog') as batch_op:
        batch_op.drop_column('critical_notes')
        batch_op.drop_column('critical_high')
        batch_op.drop_column('critical_low')