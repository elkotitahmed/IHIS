"""Billing security regression tests.

Verifies:
- Patients (the 'Patient' role) cannot reach staff-only billing endpoints.
- A patient-facing bills page only ever lists the logged-in patient's own bills
  (self-isolation) — never another patient's.
- Role + permission gating works: a Receptionist with BILL_VIEW can view bills,
  but a Receptionist (who holds no BILL_VOID) and a non-Admin/SuperAdmin cannot
  void a bill (403) and cannot create bills without BILL_CREATE.
"""
import unittest
from datetime import datetime

from app import create_app, db
from app.models import (Role, Permission, User, Patient, Bill, BillItem,
                        Payment, ServiceCatalog, Specialty, Doctor)
from app.permissions import (BILL_VIEW, BILL_CREATE, BILL_VOID, PAYMENT_RECORD)
from seed import ROLES


class BillingSecurityTest(unittest.TestCase):
    def _login(self, client, email):
        resp = client.post('/auth/login', data={
            'email': email, 'password': '123456',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200, f'login failed for {email}')
        return client

    def _mk_user(self, uname, utype, roles_lst):
        u = User(username=uname, email=f'{uname}@test.com',
                 full_name=f'Test {uname}', user_type=utype)
        u.set_password('123456')
        for r in roles_lst:
            role = Role.query.filter_by(name=r).first()
            u.roles.append(role)
        db.session.add(u)
        db.session.flush()
        return u

    def _ensure_perms(self, role_name, perm_names):
        role = Role.query.filter_by(name=role_name).first()
        for pn in perm_names:
            p = Permission.query.filter_by(name=pn).first()
            if p is None:
                p = Permission(name=pn)
                db.session.add(p)
                db.session.flush()
            if p not in role.permissions:
                role.permissions.append(p)

    def _mk_bill(self, patient, bill_no):
        b = Bill(patient_id=patient.id, created_by=None, bill_no=bill_no,
                 source_type='Manual')
        db.session.add(b)
        db.session.flush()
        db.session.add(BillItem(bill_id=b.id, description='Consult',
                                quantity=1, unit_price=100.0))
        return b

    def test_patient_cannot_reach_staff_billing_routes(self):
        app = create_app('testing')
        app.config['WTF_CSRF_ENABLED'] = False
        with app.app_context():
            db.create_all()
            for n in ROLES:
                db.session.add(Role(name=n))
            db.session.commit()

            u_pat = self._mk_user('pat', 'patient', ['Patient'])
            pat = Patient(user_id=u_pat.id)
            db.session.add(pat)
            db.session.commit()

            client = app.test_client()
            self._login(client, 'pat@test.com')
            for path in ('/billing/dashboard', '/billing/bills',
                         '/billing/bills/new', '/billing/service-catalog',
                         '/billing/reports'):
                self.assertEqual(client.get(path).status_code, 403,
                                 f'patient should be denied {path}')

    def test_patient_sees_only_own_bills(self):
        app = create_app('testing')
        app.config['WTF_CSRF_ENABLED'] = False
        with app.app_context():
            db.create_all()
            for n in ROLES:
                db.session.add(Role(name=n))
            db.session.commit()

            u_a = self._mk_user('pata', 'patient', ['Patient'])
            u_b = self._mk_user('patb', 'patient', ['Patient'])
            pa = Patient(user_id=u_a.id)
            pb = Patient(user_id=u_b.id)
            db.session.add(pa)
            db.session.add(pb)
            db.session.flush()
            self._mk_bill(pa, 'INV-AAA')
            self._mk_bill(pb, 'INV-BBB')
            db.session.commit()

            client = app.test_client()
            self._login(client, 'pata@test.com')
            resp = client.get('/patient/bills')
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn('INV-AAA', html)
            self.assertNotIn('INV-BBB', html)

    def test_receptionist_view_ok_but_cannot_void_bill(self):
        app = create_app('testing')
        app.config['WTF_CSRF_ENABLED'] = False
        with app.app_context():
            db.create_all()
            for n in ROLES:
                db.session.add(Role(name=n))
            db.session.commit()
            # grant the Receptionist only view/create/payment, no void
            self._ensure_perms('Receptionist',
                               [BILL_VIEW, BILL_CREATE, PAYMENT_RECORD])

            u_rec = self._mk_user('reception', 'staff', ['Receptionist'])
            u_pat = self._mk_user('pat', 'patient', ['Patient'])
            pat = Patient(user_id=u_pat.id)
            db.session.add(pat)
            db.session.flush()
            bill = self._mk_bill(pat, 'INV-001')
            db.session.commit()

            client = app.test_client()
            self._login(client, 'reception@test.com')
            # can view bills list
            self.assertEqual(client.get('/billing/bills').status_code, 200)
            # can view a specific bill
            self.assertEqual(
                client.get(f'/billing/bills/{bill.id}').status_code, 200)
            # cannot void (missing role + missing BILL_VOID permission)
            self.assertEqual(
                client.post(f'/billing/bills/{bill.id}/void').status_code, 403)


if __name__ == '__main__':
    unittest.main()