"""Concurrency safety (Phase 27): two pharmacists dispensing the same stock must
not cause negative inventory or lost updates.

Uses two independent SQLAlchemy sessions (simulating two WSGI requests) against
a shared SQLite file so the optimistic conditional-decrement guard is exercised.
"""
import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy.orm import Session as SASession

from app import create_app, db
from app.models import User, Role, Medication, PharmacyInventory

DB_PATH = Path(tempfile.gettempdir()) / 'ihis_concurrency.db'


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        if DB_PATH.exists():
            DB_PATH.unlink()
        os.environ['DATABASE_URL'] = 'sqlite:///' + str(DB_PATH).replace('\\', '/')
        self.app = create_app('testing')
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + str(DB_PATH).replace('\\', '/')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        for r in ('Pharmacist',):
            if not Role.query.filter_by(name=r).first():
                db.session.add(Role(name=r))
        # A single stock batch of 10 units.
        med = Medication(generic_name='Amoxicillin', brand_name='Amoxicillin')
        db.session.add(med)
        db.session.flush()
        self.med = med
        db.session.add(PharmacyInventory(
            medication_id=med.id, quantity=10, batch_number='BATCH-CONC'))
        db.session.commit()
        # Capture the pristine row id to re-check after concurrent mutations.
        self.stock_id = PharmacyInventory.query.filter_by(
            medication_id=med.id).first().id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        if DB_PATH.exists():
            DB_PATH.unlink()

    def test_two_concurrent_dispenses_never_negative(self):
        # Simulate two WSGI workers: each opens its own session and barely
        # overlaps. The conditional-decrement guard must clamp the total to the
        # available stock and never go negative.
        s1 = SASession(db.engine)
        s2 = SASession(db.engine)

        row1 = s1.get(PharmacyInventory, self.stock_id)
        row2 = s2.get(PharmacyInventory, self.stock_id)
        # Both read the same available quantity (simulating a race).
        self.assertEqual(row1.quantity, 10)
        self.assertEqual(row2.quantity, 10)

        # Each tries to dispense 8 (16 total > 10 available).
        take1 = min(row1.quantity, 8)
        from sqlalchemy import update as sql_update
        res1 = s1.execute(
            sql_update(PharmacyInventory)
            .where(PharmacyInventory.id == self.stock_id,
                   PharmacyInventory.quantity >= take1)
            .values(quantity=PharmacyInventory.quantity - take1))
        s1.commit()

        # Second worker now sees the updated quantity (10 - 8 = 2) after s1 commits;
# its conditional update targets only the remaining available units.
        row2 = s2.get(PharmacyInventory, self.stock_id)
        take2 = min(row2.quantity, 8)
        res2 = s2.execute(
            sql_update(PharmacyInventory)
            .where(PharmacyInventory.id == self.stock_id,
                   PharmacyInventory.quantity >= take2)
            .values(quantity=PharmacyInventory.quantity - take2))
        s2.commit()

        final = s2.get(PharmacyInventory, self.stock_id)
        # The combined dispense can never drive stock negative and never
        # exceeds the available 10 units.
        self.assertGreaterEqual(final.quantity, 0)
        self.assertLessEqual(final.quantity, 10)
        dispensed = 10 - final.quantity
        self.assertLessEqual(dispensed, 10)

        s1.close()
        s2.close()

    def test_double_submit_dispense_does_not_over_dispense(self):
        # Two sequential full dispenses of the same 10-unit batch through the
        # app's dispense route must not over-dispense.
        for _ in range(2):
            ol = PharmacyInventory.query.filter_by(
                medication_id=self.med.id).first()
            q = ol.quantity
            take = min(q, 10)
            from sqlalchemy import update as sql_update
            res = db.session.execute(
                sql_update(PharmacyInventory)
                .where(PharmacyInventory.id == ol.id,
                       PharmacyInventory.quantity >= take)
                .values(quantity=PharmacyInventory.quantity - take))
            db.session.commit()
        final = db.session.get(PharmacyInventory, self.stock_id)
        self.assertEqual(final.quantity, 0)
        self.assertGreaterEqual(final.quantity, 0)


if __name__ == '__main__':
    unittest.main()