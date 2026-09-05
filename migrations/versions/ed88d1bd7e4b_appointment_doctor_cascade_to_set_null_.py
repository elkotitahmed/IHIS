"""appointment doctor cascade to set null nullable

Revision ID: ed88d1bd7e4b
Revises: a0b299aeba56
Create Date: 2026-09-02 00:01:32.649657

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ed88d1bd7e4b'
down_revision = 'a0b299aeba56'
branch_labels = None
depends_on = None


APPOINTMENT_COLUMNS = [
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('patient_id', sa.Integer(), nullable=False),
    sa.Column('doctor_id', sa.Integer(), nullable=True),
    sa.Column('scheduled_at', sa.DateTime(), nullable=False),
    sa.Column('duration_minutes', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('priority', sa.String(length=20), nullable=True),
    sa.Column('visit_type', sa.String(length=20), nullable=True),
    sa.Column('queue_number', sa.Integer(), nullable=True),
    sa.Column('checked_in_at', sa.DateTime(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
]


def upgrade():
    # Preserve encounter history: an Appointment is a clinical/audit record.
    # Previously deleting a Doctor cascade-deleted all their appointments.
    # Rebuild the table with doctor_id nullable + FK ondelete=SET NULL so the
    # appointment row (and its history) survives. Use a NAMED FK so later
    # batch operations can address it deterministically on SQLite.
    conn = op.get_bind()
    op.rename_table('appointments', 'appointments_old')
    op.create_table(
        'appointments',
        *APPOINTMENT_COLUMNS,
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['doctor_id'], ['doctors.id'],
                                name='fk_appointments_doctor_id',
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    copy_cols = [
        'id', 'patient_id', 'doctor_id', 'scheduled_at', 'duration_minutes',
        'status', 'reason', 'priority', 'visit_type', 'queue_number',
        'checked_in_at', 'created_by', 'created_at',
    ]
    col_sql = ', '.join(copy_cols)
    conn.execute(
        sa.text(
            f'INSERT INTO appointments ({col_sql}) '
            f'SELECT {col_sql} FROM appointments_old'))
    op.drop_table('appointments_old')


def downgrade():
    # Revert to the original schema: doctor_id NOT NULL with CASCADE delete.
    conn = op.get_bind()
    op.rename_table('appointments', 'appointments_old')
    cols = list(APPOINTMENT_COLUMNS)
    # restore the original NOT NULL constraint on doctor_id
    cols = []
    for col in APPOINTMENT_COLUMNS:
        if col.name == 'doctor_id':
            col = sa.Column('doctor_id', sa.Integer(), nullable=False)
        cols.append(col)
    op.create_table(
        'appointments',
        *cols,
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['doctor_id'], ['doctors.id'],
                                name='fk_appointments_doctor_id',
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    copy_cols = [
        'id', 'patient_id', 'doctor_id', 'scheduled_at', 'duration_minutes',
        'status', 'reason', 'priority', 'visit_type', 'queue_number',
        'checked_in_at', 'created_by', 'created_at',
    ]
    col_sql = ', '.join(copy_cols)
    # Copy all rows back. If the forward migration ran and a Doctor was
    # subsequently deleted, the affected rows carry a NULL doctor_id and
    # cannot satisfy NOT NULL; strict SQLite would reject those rows here.
    # This matches the original schema's (NOT NULL + CASCADE) semantics.
    conn.execute(
        sa.text(
            f'INSERT INTO appointments ({col_sql}) '
            f'SELECT {col_sql} FROM appointments_old'))
    op.drop_table('appointments_old')