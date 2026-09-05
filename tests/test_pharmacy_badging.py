"""Pharmacy inventory expiry/low-stock badging and partial-dispensing queue tests."""
import re
import unittest
from datetime import date, timedelta

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, Medication,
                        PharmacyInventory, Prescription, PrescriptionItem,
                        DispensingRecord)


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class PharmacyBadgingTestCase(unittest.TestCase):
    ROLES = ['SuperAdmin', 'Admin', 'Doctor', 'Nurse', 'Patient',
             'LabTechnician', 'Radiologist', 'Pharmacist', 'Receptionist',
             'Dentist', 'Physiotherapist']

    def setUp(self):
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for n in self.ROLES:
            db.session.add(Role(name=n))
        from app.permissions import seed_permissions
        seed_permissions(db)

        self.spec = Specialty(name='General')
        db.session.add(self.spec)
        db.session.commit()
        self.pat = self._make_patient('pat@t.com')
        self.doc = self._make_user('doc@t.com', 'doctor', 'Doctor')
        self.doc_profile = Doctor.query.filter_by(user_id=self.doc.id).first()
        self.pharma = self._make_user('ph@t.com', 'pharmacist', 'Pharmacist')
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _make_user(self, email, utype, role, password='123456'):
        u = User(username=email.split('@')[0], email=email,
                 full_name='Test ' + utype, user_type=utype)
        u.set_password(password)
        u.roles.append(Role.query.filter_by(name=role).first())
        db.session.add(u)
        if utype == 'doctor':
            db.session.flush()
            db.session.add(Doctor(user_id=u.id, specialty_id=self.spec.id,
                                  license_number='L' + email[:6]))
        db.session.commit()
        return u

    def _make_patient(self, email):
        u = User(username=email.split('@')[0], email=email,
                 full_name='Pat User', user_type='patient')
        u.set_password('123456')
        u.roles.append(Role.query.filter_by(name='Patient').first())
        db.session.add(u)
        db.session.commit()
        p = Patient(user_id=u.id, mrn='MRN-' + email[:3])
        db.session.add(p)
        db.session.commit()
        return p

    def _med(self, name):
        m = Medication(generic_name=name, brand_name=name, category='Test')
        db.session.add(m)
        db.session.commit()
        return m

    def _login(self, email):
        self.client.get('/auth/logout')
        r = self.client.get('/auth/login')
        return self.client.post('/auth/login', data={
            'email': email, 'password': '123456',
            'csrf_token': _csrf(r.data)}, follow_redirects=True)

    # ---- #14 inventory badging --------------------------------------
    def _seed_inventory(self):
        today = date.today()
        med_ok = self._med('MedOk')
        med_soon = self._med('MedSoon')
        med_expired = self._med('MedExpired')
        med_exp_low = self._med('MedExpiredLow')
        items = [
            PharmacyInventory(medication_id=med_ok.id, quantity=100,
                              reorder_level=10, expiry_date=today + timedelta(days=400),
                              batch_number='B-OK'),
            PharmacyInventory(medication_id=med_soon.id, quantity=100,
                              reorder_level=10, expiry_date=today + timedelta(days=20),
                              batch_number='B-SOON'),
            PharmacyInventory(medication_id=med_expired.id, quantity=50,
                              reorder_level=10, expiry_date=today - timedelta(days=3),
                              batch_number='B-EXP'),
            PharmacyInventory(medication_id=med_exp_low.id, quantity=2,
                              reorder_level=10, expiry_date=today - timedelta(days=9),
                              batch_number='B-EXPLOW'),
        ]
        db.session.add_all(items)
        db.session.commit()
        return items

    def test_inventory_badge_precedence(self):
        items = self._seed_inventory()
        self._login('ph@t.com')
        r = self.client.get('/pharmacy/inventory')
        self.assertEqual(r.status_code, 200)
        html = r.data.decode('utf-8', 'replace')
        # Expired items are never masked by low stock.
        expired_low = PharmacyInventory.query.filter_by(
            batch_number='B-EXPLOW').first()
        row = html.split(f'<td class="cell-mono">{expired_low.id}</td>')[1]
        row = row.split('</tr>')[0]
        self.assertIn('Expired', row)
        self.assertIn('Low Stock', row)
        # Expiring-soon shows the "Expires in N days" chip.
        soon = PharmacyInventory.query.filter_by(batch_number='B-SOON').first()
        row = html.split(f'<td class="cell-mono">{soon.id}</td>')[1]
        row = row.split('</tr>')[0]
        self.assertIn('Expires ', row)
        self.assertIn('In Stock', row)
        # Healthy item: In Stock, no warning chips.
        ok = PharmacyInventory.query.filter_by(batch_number='B-OK').first()
        row = html.split(f'<td class="cell-mono">{ok.id}</td>')[1]
        row = row.split('</tr>')[0]
        self.assertIn('In Stock', row)
        self.assertNotIn('Expires', row)

    def test_inventory_filter_tabs(self):
        items = self._seed_inventory()
        self._login('ph@t.com')

        r = self.client.get('/pharmacy/inventory?f=expired')
        html = r.data.decode('utf-8', 'replace')
        expired = PharmacyInventory.query.filter_by(batch_number='B-EXP').first()
        healthy = PharmacyInventory.query.filter_by(batch_number='B-OK').first()
        self.assertIn(str(expired.id), html)
        self.assertNotIn(f'>{healthy.id}<', html)

        r = self.client.get('/pharmacy/inventory?f=low')
        html = r.data.decode('utf-8', 'replace')
        low = PharmacyInventory.query.filter_by(batch_number='B-EXPLOW').first()
        ok = PharmacyInventory.query.filter_by(batch_number='B-OK').first()
        self.assertIn(str(low.id), html)
        self.assertNotIn(f'>{ok.id}<', html)

        r = self.client.get('/pharmacy/inventory?f=expiring')
        html = r.data.decode('utf-8', 'replace')
        soon = PharmacyInventory.query.filter_by(batch_number='B-SOON').first()
        ok = PharmacyInventory.query.filter_by(batch_number='B-OK').first()
        self.assertIn(str(soon.id), html)
        self.assertNotIn(f'>{ok.id}<', html)

    # ---- #15 partial dispensing visibility ---------------------------
    def _seed_rx(self):
        med_a = self._med('RxMedA')
        med_b = self._med('RxMedB')
        rx = Prescription(patient_id=self.pat.id, doctor_id=self.doc_profile.id,
                          status='Active')
        db.session.add(rx)
        db.session.flush()
        i1 = PrescriptionItem(prescription_id=rx.id, medication_id=med_a.id,
                              quantity=10, status='Active')
        i2 = PrescriptionItem(prescription_id=rx.id, medication_id=med_b.id,
                              quantity=5, status='Active')
        db.session.add_all([i1, i2])
        db.session.flush()
        db.session.add(DispensingRecord(prescription_id=rx.id, item_id=i1.id,
                                        pharmacist_id=self.pharma.id,
                                        quantity=4))
        db.session.commit()
        return rx, i1

    def test_queue_partial_badge_and_aggregate(self):
        rx_partial, _ = self._seed_rx()
        self._login('ph@t.com')
        r = self.client.get('/pharmacy/prescriptions')
        self.assertEqual(r.status_code, 200)
        html = r.data.decode('utf-8', 'replace')
        self.assertIn('Partially Dispensed', html)
        self.assertIn(f'Rx #{rx_partial.id}', html)
        # per-item remaining marker is surfaced
        self.assertIn('(rem 6)', html)

    def test_queue_excludes_cancelled_and_dispensed(self):
        rx_partial, _ = self._seed_rx()
        med = self._med('RxMedC')
        cancelled = Prescription(patient_id=self.pat.id,
                                 doctor_id=self.doc_profile.id,
                                 status='Cancelled')
        db.session.add(cancelled)
        db.session.flush()
        db.session.add(PrescriptionItem(prescription_id=cancelled.id,
                                        medication_id=med.id, quantity=3,
                                        status='Cancelled'))
        dispensed = Prescription(patient_id=self.pat.id,
                                 doctor_id=self.doc_profile.id,
                                 status='Dispensed')
        db.session.add(dispensed)
        db.session.flush()
        d1 = PrescriptionItem(prescription_id=dispensed.id, medication_id=med.id,
                              quantity=3, status='Dispensed')
        db.session.add(d1)
        db.session.flush()
        db.session.add(DispensingRecord(prescription_id=dispensed.id,
                                        item_id=d1.id,
                                        pharmacist_id=self.pharma.id, quantity=3))
        db.session.commit()
        self._login('ph@t.com')
        html = self.client.get('/pharmacy/prescriptions').data.decode('utf-8', 'replace')
        self.assertIn(f'Rx #{rx_partial.id}', html)
        self.assertNotIn(f'Rx #{cancelled.id}', html)
        self.assertNotIn(f'Rx #{dispensed.id}', html)

    def test_queue_partial_rx_sorted_first(self):
        rx_partial, _ = self._seed_rx()
        med = self._med('RxMedP')
        rx_pending = Prescription(patient_id=self.pat.id,
                                  doctor_id=self.doc_profile.id,
                                  status='Active')
        db.session.add(rx_pending)
        db.session.flush()
        db.session.add(PrescriptionItem(prescription_id=rx_pending.id,
                                        medication_id=med.id, quantity=4,
                                        status='Active'))
        db.session.commit()
        self._login('ph@t.com')
        html = self.client.get('/pharmacy/prescriptions').data.decode('utf-8', 'replace')
        self.assertLess(
            html.index(f'Rx #{rx_partial.id}'),
            html.index(f'Rx #{rx_pending.id}'),
            'partially dispensed prescriptions should rise to the top of the queue')


if __name__ == '__main__':
    unittest.main()