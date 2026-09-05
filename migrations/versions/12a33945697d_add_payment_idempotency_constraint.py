"""add_payment_idempotency_constraint

Revision ID: 12a33945697d
Revises: 6e4b0dc64295
Create Date: 2026-09-02 21:42:20.559106

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '12a33945697d'
down_revision = '6e4b0dc64295'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_payments_bill_reference',
                                          ['bill_id', 'reference'])


def downgrade():
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_constraint('uq_payments_bill_reference', type_='unique')