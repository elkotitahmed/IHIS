"""AI platform (usage audit + cache) and critical-alert lifecycle fields

Revision ID: c3d4e5f60718
Revises: b7c2e9d41f05
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa

revision = 'c3d4e5f60718'
down_revision = 'b7c2e9d41f05'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'ai_usage_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('role', sa.String(length=40), nullable=True),
        sa.Column('feature', sa.String(length=80), nullable=False),
        sa.Column('patient_id', sa.Integer(), nullable=True),
        sa.Column('provider', sa.String(length=20), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=True),
        sa.Column('http_status', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('cache_hit', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('accepted', sa.Boolean(), nullable=True),
        sa.Column('detail', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL',
                                name='fk_ai_usage_logs_user'),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='SET NULL',
                                name='fk_ai_usage_logs_patient'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ai_usage_logs_feature', 'ai_usage_logs', ['feature'])
    op.create_index('ix_ai_usage_logs_status', 'ai_usage_logs', ['status'])
    op.create_index('ix_ai_usage_logs_created_at', 'ai_usage_logs', ['created_at'])

    op.create_table(
        'ai_cache_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cache_key', sa.String(length=64), nullable=False),
        sa.Column('feature', sa.String(length=80), nullable=True),
        sa.Column('patient_id', sa.Integer(), nullable=True),
        sa.Column('payload', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cache_key', name='uq_ai_cache_entries_key'),
    )
    op.create_index('ix_ai_cache_entries_patient_id', 'ai_cache_entries', ['patient_id'])
    op.create_index('ix_ai_cache_entries_expires_at', 'ai_cache_entries', ['expires_at'])

    with op.batch_alter_table('clinical_alerts') as b:
        b.add_column(sa.Column('ai_assisted', sa.Boolean(), nullable=False,
                               server_default=sa.false()))
        b.add_column(sa.Column('confidence', sa.Float(), nullable=True))
        b.add_column(sa.Column('rationale', sa.Text(), nullable=True))
        b.add_column(sa.Column('action_taken', sa.Text(), nullable=True))
        b.add_column(sa.Column('assigned_to', sa.Integer(), nullable=True))
        b.add_column(sa.Column('started_by', sa.Integer(), nullable=True))
        b.add_column(sa.Column('started_at', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('escalated_at', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('escalation_note', sa.Text(), nullable=True))
        b.create_foreign_key('fk_clinical_alerts_assigned_to', 'users',
                             ['assigned_to'], ['id'], ondelete='SET NULL')
        b.create_foreign_key('fk_clinical_alerts_started_by', 'users',
                             ['started_by'], ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('clinical_alerts') as b:
        b.drop_constraint('fk_clinical_alerts_started_by', type_='foreignkey')
        b.drop_constraint('fk_clinical_alerts_assigned_to', type_='foreignkey')
        for col in ('escalation_note', 'escalated_at', 'started_at', 'started_by',
                    'assigned_to', 'action_taken', 'rationale', 'confidence', 'ai_assisted'):
            b.drop_column(col)
    op.drop_index('ix_ai_cache_entries_expires_at', table_name='ai_cache_entries')
    op.drop_index('ix_ai_cache_entries_patient_id', table_name='ai_cache_entries')
    op.drop_table('ai_cache_entries')
    op.drop_index('ix_ai_usage_logs_created_at', table_name='ai_usage_logs')
    op.drop_index('ix_ai_usage_logs_status', table_name='ai_usage_logs')
    op.drop_index('ix_ai_usage_logs_feature', table_name='ai_usage_logs')
    op.drop_table('ai_usage_logs')
