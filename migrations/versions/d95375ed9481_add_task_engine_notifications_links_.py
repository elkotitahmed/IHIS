"""add task engine, notifications links, workflows

Revision ID: d95375ed9481
Revises: da9c85479549
Create Date: 2026-08-30 22:35:51.915300

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd95375ed9481'
down_revision = 'da9c85479549'
branch_labels = None
depends_on = None


def upgrade():
    # New tables: must be created before the batch alters that add foreign
    # keys pointing at them (e.g. dental_procedures.treatment_plan_id).
    op.create_table(
        'dental_treatment_plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('patient_id', sa.Integer(), nullable=False),
        sa.Column('dentist_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('diagnosis', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['dentist_id'], ['dentists.id'], ),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('task_type', sa.String(length=50), nullable=True),
        sa.Column('patient_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('assigned_to', sa.Integer(), nullable=True),
        sa.Column('assigned_role', sa.String(length=50), nullable=True),
        sa.Column('department', sa.String(length=100), nullable=True),
        sa.Column('priority', sa.String(length=20), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('due_at', sa.DateTime(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('related_resource_type', sa.String(length=50), nullable=True),
        sa.Column('related_resource_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['assigned_to'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_tasks_created_at', 'tasks', ['created_at'])
    op.create_index('ix_tasks_status', 'tasks', ['status'])
    op.create_table(
        'task_activities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(length=50), nullable=True),
        sa.Column('from_status', sa.String(length=20), nullable=True),
        sa.Column('to_status', sa.String(length=20), nullable=True),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'stock_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('inventory_id', sa.Integer(), nullable=True),
        sa.Column('medication_id', sa.Integer(), nullable=False),
        sa.Column('tx_type', sa.String(length=30), nullable=False),
        sa.Column('quantity_change', sa.Integer(), nullable=False),
        sa.Column('quantity_after', sa.Integer(), nullable=True),
        sa.Column('unit_cost', sa.Float(), nullable=True),
        sa.Column('reference', sa.String(length=100), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['inventory_id'], ['pharmacy_inventory.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['medication_id'], ['medications.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_stock_transactions_created_at', 'stock_transactions', ['created_at'])
    op.create_table(
        'intake_output',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('patient_id', sa.Integer(), nullable=False),
        sa.Column('nurse_id', sa.Integer(), nullable=True),
        sa.Column('intake_type', sa.String(length=50), nullable=True),
        sa.Column('intake_ml', sa.Integer(), nullable=True),
        sa.Column('output_type', sa.String(length=50), nullable=True),
        sa.Column('output_ml', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['nurse_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('appointments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('visit_type', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('queue_number', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('checked_in_at', sa.DateTime(), nullable=True))

    with op.batch_alter_table('dental_charts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('surface', sa.String(length=20), nullable=True))

    with op.batch_alter_table('dental_procedures', schema=None) as batch_op:
        batch_op.add_column(sa.Column('treatment_plan_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('status', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('scheduled_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('materials', sa.String(length=250), nullable=True))
        batch_op.add_column(sa.Column('completed_at', sa.DateTime(), nullable=True))
        batch_op.create_foreign_key('fk_dental_procedures_treatment_plan_id', 'dental_treatment_plans', ['treatment_plan_id'], ['id'], ondelete='SET NULL')

    with op.batch_alter_table('dental_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('complaint', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('examination_findings', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('diagnosis', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('periodontal_notes', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('treatment_plan', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))

    with op.batch_alter_table('lab_orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('specimen_type', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('accession_number', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('barcode', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('collected_by', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('specimen_status', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('collection_time', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('received_at_lab', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('rejection_reason', sa.String(length=250), nullable=True))
        batch_op.add_column(sa.Column('reordered_from', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_lab_orders_accession_number'), ['accession_number'], unique=False)
        batch_op.create_foreign_key('fk_lab_orders_collected_by', 'users', ['collected_by'], ['id'], ondelete='SET NULL')

    with op.batch_alter_table('lab_results', schema=None) as batch_op:
        batch_op.add_column(sa.Column('result_unit', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('is_critical', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('qualitative', sa.String(length=50), nullable=True))

    with op.batch_alter_table('medication_administrations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('prescription_item_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('medication_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('scheduled_time', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('route', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('reason', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('created_at', sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f('ix_medication_administrations_scheduled_time'), ['scheduled_time'], unique=False)
        batch_op.create_index(batch_op.f('ix_medication_administrations_status'), ['status'], unique=False)
        batch_op.create_foreign_key('fk_med_admins_prescription_item_id', 'prescription_items', ['prescription_item_id'], ['id'])
        batch_op.create_foreign_key('fk_med_admins_medication_id', 'medications', ['medication_id'], ['id'])

    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('entity_type', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('entity_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_notifications_created_at'), ['created_at'], unique=False)

    with op.batch_alter_table('patients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mrn', sa.String(length=30), nullable=True))
        batch_op.create_index(batch_op.f('ix_patients_mrn'), ['mrn'], unique=True)

    with op.batch_alter_table('radiology_orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('scheduled_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('arrived_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('performed_by', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('performed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('technical_notes', sa.Text(), nullable=True))
        batch_op.create_foreign_key('fk_radiology_orders_performed_by', 'users', ['performed_by'], ['id'], ondelete='SET NULL')

    with op.batch_alter_table('therapy_assessments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('assessment_type', sa.String(length=30), nullable=True))

    with op.batch_alter_table('therapy_plans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('precautions', sa.Text(), nullable=True))

    with op.batch_alter_table('therapy_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('started_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('settled_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('pain_before', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('pain_after', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('exercises_performed', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('modalities', sa.String(length=250), nullable=True))
        batch_op.add_column(sa.Column('patient_response', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('adherence', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('followup_required', sa.Boolean(), nullable=True))

    with op.batch_alter_table('vital_signs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pain_score', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('blood_glucose', sa.Float(), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    op.drop_table('intake_output')
    op.drop_index('ix_stock_transactions_created_at', table_name='stock_transactions')
    op.drop_table('stock_transactions')
    op.drop_table('task_activities')
    op.drop_index('ix_tasks_status', table_name='tasks')
    op.drop_index('ix_tasks_created_at', table_name='tasks')
    op.drop_table('tasks')
    op.drop_table('dental_treatment_plans')
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('vital_signs', schema=None) as batch_op:
        batch_op.drop_column('blood_glucose')
        batch_op.drop_column('pain_score')

    with op.batch_alter_table('therapy_sessions', schema=None) as batch_op:
        batch_op.drop_column('followup_required')
        batch_op.drop_column('adherence')
        batch_op.drop_column('patient_response')
        batch_op.drop_column('modalities')
        batch_op.drop_column('exercises_performed')
        batch_op.drop_column('pain_after')
        batch_op.drop_column('pain_before')
        batch_op.drop_column('settled_at')
        batch_op.drop_column('started_at')

    with op.batch_alter_table('therapy_plans', schema=None) as batch_op:
        batch_op.drop_column('precautions')

    with op.batch_alter_table('therapy_assessments', schema=None) as batch_op:
        batch_op.drop_column('assessment_type')

    with op.batch_alter_table('radiology_orders', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_column('technical_notes')
        batch_op.drop_column('performed_at')
        batch_op.drop_column('performed_by')
        batch_op.drop_column('arrived_at')
        batch_op.drop_column('scheduled_at')

    with op.batch_alter_table('patients', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patients_mrn'))
        batch_op.drop_column('mrn')

    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_created_at'))
        batch_op.drop_column('entity_id')
        batch_op.drop_column('entity_type')

    with op.batch_alter_table('medication_administrations', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_medication_administrations_status'))
        batch_op.drop_index(batch_op.f('ix_medication_administrations_scheduled_time'))
        batch_op.drop_column('created_at')
        batch_op.drop_column('reason')
        batch_op.drop_column('route')
        batch_op.drop_column('scheduled_time')
        batch_op.drop_column('medication_id')
        batch_op.drop_column('prescription_item_id')

    with op.batch_alter_table('lab_results', schema=None) as batch_op:
        batch_op.drop_column('qualitative')
        batch_op.drop_column('is_critical')
        batch_op.drop_column('result_unit')

    with op.batch_alter_table('lab_orders', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_lab_orders_accession_number'))
        batch_op.drop_column('reordered_from')
        batch_op.drop_column('rejection_reason')
        batch_op.drop_column('received_at_lab')
        batch_op.drop_column('collection_time')
        batch_op.drop_column('specimen_status')
        batch_op.drop_column('collected_by')
        batch_op.drop_column('barcode')
        batch_op.drop_column('accession_number')
        batch_op.drop_column('specimen_type')

    with op.batch_alter_table('dental_records', schema=None) as batch_op:
        batch_op.drop_column('updated_at')
        batch_op.drop_column('treatment_plan')
        batch_op.drop_column('periodontal_notes')
        batch_op.drop_column('diagnosis')
        batch_op.drop_column('examination_findings')
        batch_op.drop_column('complaint')

    with op.batch_alter_table('dental_procedures', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_column('completed_at')
        batch_op.drop_column('materials')
        batch_op.drop_column('scheduled_at')
        batch_op.drop_column('status')
        batch_op.drop_column('treatment_plan_id')

    with op.batch_alter_table('dental_charts', schema=None) as batch_op:
        batch_op.drop_column('surface')

    with op.batch_alter_table('appointments', schema=None) as batch_op:
        batch_op.drop_column('checked_in_at')
        batch_op.drop_column('queue_number')
        batch_op.drop_column('visit_type')

    # ### end Alembic commands ###
