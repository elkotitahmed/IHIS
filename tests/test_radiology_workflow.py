"""Radiology full workflow integration tests (scenario: study lifecycle)."""
import re
import unittest

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, ImagingType,
                        RadiologyOrder, RadiologyReport, Task, Notification)


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class RadiologyWorkflowTestCase(unittest.TestCase):
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
        db.session.commit()
        from app.permissions import seed_permissions
        seed_permissions(db)
        self.doc = self._make_user('doc@t.com', 'doctor', 'Doctor')
        self.doc_u = User.query.filter_by(email='doc@t.com').first()
        self.rad = self._make_user('rad@t.com', 'rad', 'Radiologist')
        self.rad_u = User.query.filter_by(email='rad@t.com').first()
        # patient + imaging type
        self.pat = User(username='pat', email='pat@t.com', full_name='Test Patient',
                        user_type='patient')
        self.pat.set_password('123456')
        self.pat.roles.append(Role.query.filter_by(name='Patient').first())
        db.session.add(self.pat)
        db.session.commit()
        self.patient = Patient(user_id=self.pat.id)
        db.session.add(self.patient)
        db.session.commit()
        self.patient_id = self.patient.id
        self.img = ImagingType(name='Chest X-Ray', price=80.0)
        db.session.add(self.img)
        db.session.commit()
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
            spec = Specialty(name='General')
            db.session.add(spec)
            db.session.commit()
            db.session.add(Doctor(user_id=u.id, specialty_id=spec.id))
        db.session.commit()
        return u

    def _login(self, email):
        self.client.get('/auth/logout')
        page = self.client.get('/auth/login')
        tok = _csrf(page.data)
        return self.client.post('/auth/login', data={
            'email': email, 'password': '123456', 'csrf_token': tok,
        }, follow_redirects=True)

    def _create_order(self):
        self._login(self.doc_u.email)
        page = self.client.get('/radiology/order/new')
        tok = _csrf(page.data)
        self.client.post('/radiology/order/new', data={
            'patient_id': self.patient_id, 'imaging_type_id': self.img.id,
            'priority': 'Normal', 'csrf_token': tok,
        }, follow_redirects=True)
        return RadiologyOrder.query.first()

    def test_orders_create_task_and_notify(self):
        o = self._create_order()
        self.assertIsNotNone(o)
        self.assertEqual(o.status, 'Pending')
        # task created for radiology
        task = Task.query.filter_by(related_resource_type='radiology_order',
                                    related_resource_id=o.id).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.department, 'Radiology')
        # notify radiologists
        n = Notification.query.filter_by(user_id=self.rad_u.id,
                                          entity_type='radiology_order',
                                          entity_id=o.id).first()
        self.assertIsNotNone(n)

    def test_full_lifecycle(self):
        o = self._create_order()
        self._login(self.rad_u.email)
        # schedule
        self._post(f'/radiology/orders/{o.id}/schedule', {'scheduled_at': '2026-09-01T10:00'})
        o = db.session.get(RadiologyOrder, o.id)
        self.assertEqual(o.status, 'Scheduled')
        # arrive
        self._post(f'/radiology/orders/{o.id}/arrive')
        o = db.session.get(RadiologyOrder, o.id)
        self.assertEqual(o.status, 'Arrived')
        self.assertIsNotNone(o.arrived_at)
        # perform
        self._post(f'/radiology/orders/{o.id}/perform', {'technical_notes': 'done'})
        o = db.session.get(RadiologyOrder, o.id)
        self.assertEqual(o.status, 'Performed')
        self.assertEqual(o.performed_by, self.rad_u.id)
        # report
        self._post_report(o.id, findings='Normal', impression='Clear', recommendation='None')
        o = db.session.get(RadiologyOrder, o.id)
        self.assertEqual(o.status, 'Reported')
        r = RadiologyReport.query.filter_by(order_id=o.id).first()
        self.assertEqual(r.status, 'Draft')
        # sign
        self._post(f'/radiology/orders/{o.id}/sign')
        r = db.session.get(RadiologyReport, r.id)
        self.assertEqual(r.status, 'Signed')
        self.assertEqual(r.signed_by, self.rad_u.id)
        # doctor + patient notified
        doc_n = Notification.query.filter_by(user_id=self.doc_u.id,
                                              entity_type='radiology_order',
                                              entity_id=o.id).first()
        self.assertIsNotNone(doc_n)
        pat_n = Notification.query.filter_by(user_id=self.pat.id,
                                              entity_type='radiology_order',
                                              entity_id=o.id).first()
        self.assertIsNotNone(pat_n)
        # bill created
        from app.models import Bill
        bill = Bill.query.filter_by(source_type='Radiology', source_id=o.id).first()
        self.assertIsNotNone(bill)

    def test_illegal_skip_perform(self):
        o = self._create_order()
        self._login(self.rad_u.email)
        # try to sign report when order is still Pending
        self._post(f'/radiology/orders/{o.id}/sign')
        o = db.session.get(RadiologyOrder, o.id)
        self.assertEqual(o.status, 'Pending')
        r = RadiologyReport.query.filter_by(order_id=o.id).first()
        self.assertIsNone(r)

    def _post(self, url, data=None):
        page = self.client.get('/radiology/orders')
        tok = _csrf(page.data)
        d = dict(data or {})
        d['csrf_token'] = tok
        return self.client.post(url, data=d, follow_redirects=True)

    def _post_report(self, oid, findings, impression, recommendation):
        page = self.client.get(f'/radiology/orders/{oid}/report')
        tok = _csrf(page.data)
        return self.client.post(f'/radiology/orders/{oid}/report', data={
            'findings': findings, 'impression': impression,
            'recommendation': recommendation, 'csrf_token': tok,
        }, follow_redirects=True)


if __name__ == '__main__':
    unittest.main()
