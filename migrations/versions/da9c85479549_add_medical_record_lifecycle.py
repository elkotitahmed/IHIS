"""Add medical record lifecycle (sign/amend)

Introduces the Draft -> Signed clinical-record lifecycle for medical records,
mirroring the lab-result and radiology-report flows: signed records are
immutable and amendments must reopen them and be re-signed.

Revision ID: da9c85479549
Revises: a131a27bf5e5
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa


revision = 'da9c85479549'
down_revision = 'a131a27bf5e5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('medical_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=20), nullable=False,
                                      server_default='Draft'))
        batch_op.add_column(sa.Column('signed_by', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('signed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('amended_from_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_medical_records_signed_by_users', 'users',
                                    ['signed_by'], ['id'], ondelete='SET NULL')
        batch_op.create_foreign_key('fk_medical_records_amended_from', 'medical_records',
                                    ['amended_from_id'], ['id'], ondelete='SET NULL')
        batch_op.create_index('ix_medical_records_signed_by', ['signed_by'], unique=False)


def downgrade():
    with op.batch_alter_table('medical_records', schema=None) as batch_op:
        batch_op.drop_index('ix_medical_records_signed_by')
        batch_op.drop_constraint('fk_medical_records_amended_from', type_='foreignkey')
        batch_op.drop_constraint('fk_medical_records_signed_by_users', type_='foreignkey')
        batch_op.drop_column('amended_from_id')
        batch_op.drop_column('signed_at')
        batch_op.drop_column('signed_by')
        batch_op.drop_column('status')
