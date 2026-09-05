"""add clinical order sets, templates, reminders and result acknowledgements

Revision ID: f1b1a6907bef
Revises: 8a75a265845d
Create Date: 2026-09-05 16:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1b1a6907bef'
down_revision = '8a75a265845d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('order_sets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('category', sa.String(length=80), nullable=True),
    sa.Column('specialty_id', sa.Integer(), nullable=True),
    sa.Column('department_id', sa.Integer(), nullable=True),
    sa.Column('is_published', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['specialty_id'], ['specialties.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_order_sets_name'), 'order_sets', ['name'], unique=True)

    op.create_table('clinician_templates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('template_type', sa.String(length=30), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('specialty_id', sa.Integer(), nullable=True),
    sa.Column('sections', sa.Text(), nullable=True),
    sa.Column('allowed_roles', sa.String(length=200), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['specialty_id'], ['specialties.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_clinician_templates_title'), 'clinician_templates', ['title'], unique=True)

    op.create_table('clinical_reminders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('patient_id', sa.Integer(), nullable=False),
    sa.Column('reminder_type', sa.String(length=30), nullable=False),
    sa.Column('title', sa.String(length=250), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('priority', sa.String(length=20), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('source_type', sa.String(length=50), nullable=True),
    sa.Column('source_id', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('completed_by', sa.Integer(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['completed_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_clinical_reminders_due_date'), 'clinical_reminders', ['due_date'], unique=False)
    op.create_index(op.f('ix_clinical_reminders_patient_id'), 'clinical_reminders', ['patient_id'], unique=False)
    op.create_index(op.f('ix_clinical_reminders_reminder_type'), 'clinical_reminders', ['reminder_type'], unique=False)
    op.create_index(op.f('ix_clinical_reminders_status'), 'clinical_reminders', ['status'], unique=False)

    op.create_table('result_acknowledgements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('patient_id', sa.Integer(), nullable=False),
    sa.Column('result_type', sa.String(length=20), nullable=False),
    sa.Column('result_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=True),
    sa.Column('ack_by', sa.Integer(), nullable=True),
    sa.Column('ack_at', sa.DateTime(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['ack_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_result_acknowledgements_patient_id'), 'result_acknowledgements', ['patient_id'], unique=False)

    op.create_table('order_set_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_set_id', sa.Integer(), nullable=False),
    sa.Column('item_type', sa.String(length=20), nullable=False),
    sa.Column('lab_test_id', sa.Integer(), nullable=True),
    sa.Column('imaging_type_id', sa.Integer(), nullable=True),
    sa.Column('medication_id', sa.Integer(), nullable=True),
    sa.Column('dosage', sa.String(length=150), nullable=True),
    sa.Column('frequency', sa.String(length=80), nullable=True),
    sa.Column('duration', sa.String(length=80), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=True),
    sa.Column('instructions', sa.String(length=300), nullable=True),
    sa.Column('referral_specialty_id', sa.Integer(), nullable=True),
    sa.Column('priority', sa.String(length=20), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['imaging_type_id'], ['imaging_types.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['lab_test_id'], ['lab_test_catalog.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['medication_id'], ['medications.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['order_set_id'], ['order_sets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['referral_specialty_id'], ['specialties.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_order_set_items_order_set_id'), 'order_set_items', ['order_set_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_order_set_items_order_set_id'), table_name='order_set_items')
    op.drop_table('order_set_items')
    op.drop_index(op.f('ix_result_acknowledgements_patient_id'), table_name='result_acknowledgements')
    op.drop_table('result_acknowledgements')
    op.drop_index(op.f('ix_clinical_reminders_status'), table_name='clinical_reminders')
    op.drop_index(op.f('ix_clinical_reminders_reminder_type'), table_name='clinical_reminders')
    op.drop_index(op.f('ix_clinical_reminders_patient_id'), table_name='clinical_reminders')
    op.drop_index(op.f('ix_clinical_reminders_due_date'), table_name='clinical_reminders')
    op.drop_table('clinical_reminders')
    op.drop_index(op.f('ix_clinician_templates_title'), table_name='clinician_templates')
    op.drop_table('clinician_templates')
    op.drop_index(op.f('ix_order_sets_name'), table_name='order_sets')
    op.drop_table('order_sets')
