"""Delete EVERY patient and all of their data from the development database.

    python scripts/purge_patients.py            # asks for no confirmation; backs up first
    python scripts/purge_patients.py --dry-run  # only report what would go

Refuses to run against anything but a local SQLite database. A copy of the
database file is written next to it (``ihis.db.pre-purge-<timestamp>``) before
any row is touched, so the operation is reversible by copying the file back.

What goes:
* every row in every table that references ``patients`` directly or through
  another patient-owned row (prescriptions -> items -> dispensing records, lab
  orders -> results, admissions, alerts, tasks, timeline, bills, dental,
  therapy, imaging safety, reconciliations, interventions ...) — SQLite's own
  ON DELETE CASCADE / SET NULL rules are used, with foreign keys switched on
  for this connection only;
* the portal user account of each patient (``users.user_type = 'patient'``)
  and rows owned by that account (notifications, messages, audit rows);
* staff notifications that pointed at deleted clinical entities;
* beds occupied by deleted admissions are released.

Catalogues (users/staff, wards, medications, lab tests, order sets) stay.
"""
import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('FLASK_CONFIG', 'development')

from sqlalchemy import text  # noqa: E402

from app import create_app, db  # noqa: E402

PATIENT_ENTITY_TYPES = ('prescription', 'lab_order', 'radiology_order', 'admission', 'appointment', 'referral',
                        'pharmacy_intervention', 'clinical_alert', 'task', 'bill', 'patient', 'result',
                        'lab_result', 'medical_record', 'reconciliation', 'follow_up', 'patient_document',
                        'therapy_session', 'dental_procedure', 'critical_finding')


def main(dry_run=False):
    app = create_app(os.environ.get('FLASK_CONFIG', 'development'))
    with app.app_context():
        uri = app.config['SQLALCHEMY_DATABASE_URI']
        if not uri.startswith('sqlite:'):
            raise SystemExit('Refusing: purge is only allowed on a local SQLite database.')
        db_path = uri.replace('sqlite:///', '')
        if not os.path.isfile(db_path):
            raise SystemExit(f'Database file not found: {db_path}')

        from app.models import Admission, Bed, Patient, User
        patients = Patient.query.all()
        pusers = User.query.filter_by(user_type='patient').all()
        print(f'Patients: {len(patients)}  patient accounts: {len(pusers)}')
        for p in patients:
            print(f'  - #{p.id} {p.mrn or "-"} {p.user.full_name if p.user else "?"}')
        if dry_run:
            print('Dry run: nothing deleted.')
            return
        if not patients and not pusers:
            print('Nothing to delete.')
            return

        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = f'{db_path}.pre-purge-{stamp}'
        shutil.copy2(db_path, backup)
        print(f'Backup written: {backup}')

        # Release beds of active admissions before the rows disappear.
        bed_ids = [a.bed_id for a in Admission.query.filter_by(status='Admitted').all() if a.bed_id]
        if bed_ids:
            Bed.query.filter(Bed.id.in_(bed_ids)).update({Bed.status: 'Available'}, synchronize_session=False)
        db.session.commit()

        user_ids = [u.id for u in pusers] + [p.user_id for p in patients if p.user_id]
        user_ids = sorted(set(user_ids))
        patient_ids = [p.id for p in patients]
        db.session.remove()

        con = db.engine.raw_connection()
        try:
            cur = con.cursor()
            cur.execute('PRAGMA foreign_keys = ON')
            if patient_ids:
                cur.execute(f'DELETE FROM patients WHERE id IN ({",".join("?" * len(patient_ids))})', patient_ids)
                print(f'patients deleted: {cur.rowcount}')
            if user_ids:
                # Rows whose FK to users has no ON DELETE rule
                cur.execute(f'UPDATE appointments SET created_by = NULL WHERE created_by IN ({",".join("?" * len(user_ids))})', user_ids)
                cur.execute(f'DELETE FROM users WHERE id IN ({",".join("?" * len(user_ids))})', user_ids)
                print(f'patient accounts deleted: {cur.rowcount}')
            ph = ",".join("?" * len(PATIENT_ENTITY_TYPES))
            cur.execute(f'DELETE FROM notifications WHERE entity_type IN ({ph})', PATIENT_ENTITY_TYPES)
            print(f'stale notifications deleted: {cur.rowcount}')
            # Tables that carry a patient_id without a foreign key (e.g. AI cache rows)
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            for (t,) in cur.fetchall():
                cur.execute(f'PRAGMA table_info({t})')
                if 'patient_id' in [r[1] for r in cur.fetchall()]:
                    cur.execute(f'DELETE FROM {t} WHERE patient_id IS NOT NULL AND patient_id NOT IN (SELECT id FROM patients)')
                    if cur.rowcount:
                        print(f'orphans removed from {t}: {cur.rowcount}')
            con.commit()
        finally:
            con.close()

        # Verify: no table still holds a patient_id.
        leftovers = {}
        with db.engine.connect() as conn:
            conn.execute(text('PRAGMA foreign_keys = ON'))
            names = [r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))]
            for t in names:
                cols = [r[1] for r in conn.execute(text(f'PRAGMA table_info({t})'))]
                if 'patient_id' in cols:
                    n = conn.execute(text(f'SELECT COUNT(*) FROM {t} WHERE patient_id IS NOT NULL')).scalar()
                    if n:
                        leftovers[t] = n
            fk = conn.execute(text('PRAGMA foreign_key_check')).fetchall()
        if leftovers:
            print('WARNING: rows still referencing patients:', leftovers)
        else:
            print('Verified: no table references a patient any more.')
        if fk:
            print('WARNING: foreign-key check reports', len(fk), 'dangling rows (pre-existing?)', fk[:5])
        print('Done.')


if __name__ == '__main__':
    main(dry_run='--dry-run' in sys.argv)
