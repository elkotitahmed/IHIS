"""Radiology critical-finding engine, end to end.

Radiologist report → analysis (rules + local model) → ClinicalAlert →
urgent Task → physician notification → banner → acknowledge → action →
resolve → audit. Also: a non-critical report raises nothing.
"""
import unittest
from datetime import timedelta
from unittest import mock

from app import create_app, db
from app.models import (AuditLog, ClinicalAlert, CriticalFindingNotification, Doctor,
                        ImagingType, Notification, Patient, RadiologyOrder, RadiologyReport,
                        Role, Specialty, Task, User)
from app.permissions import seed_permissions
from app.services import alerts as alert_svc
from app.services import radiology_critical as rc
from app.services.ai import platform
from app.utils import utcnow
from seed import ROLES


class RadBase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.roles = {}
        for name in ROLES:
            r = Role(name=name)
            db.session.add(r)
            self.roles[name] = r
        db.session.commit()
        seed_permissions(db)
        platform.reset_runtime_state()
        self.spec = Specialty(name='General')
        db.session.add(self.spec)
        db.session.commit()
        self.client = self.app.test_client()
        self.doc = self._user('doc', 'doctor', 'Doctor')
        self.rad = self._user('rad', 'radiologist', 'Radiologist')
        self.admin = self._user('adm', 'admin', 'Admin')
        pu = User(username='pat', email='pat@t.com', full_name='Patient Pat', user_type='patient')
        pu.set_password('123456')
        pu.roles.append(self.roles['Patient'])
        db.session.add(pu)
        db.session.flush()
        self.patient = Patient(user_id=pu.id)
        db.session.add(self.patient)
        self.itype = ImagingType(name='Chest X-ray', price=50.0)
        db.session.add(self.itype)
        db.session.commit()

    def tearDown(self):
        platform.reset_runtime_state()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _user(self, name, utype, role):
        u = User(username=name, email=f'{name}@t.com', full_name=f'Test {name}', user_type=utype)
        u.set_password('123456')
        u.roles.append(self.roles[role])
        db.session.add(u)
        db.session.flush()
        if role == 'Doctor':
            db.session.add(Doctor(user_id=u.id, specialty_id=self.spec.id, consultation_fee=1))
        db.session.commit()
        return u

    def login(self, email):
        self.client.get('/auth/logout')
        return self.client.post('/auth/login', data={'email': email, 'password': '123456'})

    def order(self):
        o = RadiologyOrder(patient_id=self.patient.id, doctor_id=self.doc.doctor_profile.id,
                           imaging_type_id=self.itype.id, status='Performed')
        db.session.add(o)
        db.session.commit()
        return o


class RuleEngineTests(RadBase):
    def test_rules_detect_and_respect_negation(self):
        f = rc.rule_scan('Large right pneumothorax with mediastinal shift.')
        self.assertEqual(f[0]['finding'], 'Pneumothorax')
        self.assertEqual(f[0]['severity'], 'CRITICAL')
        self.assertEqual(rc.rule_scan('No pneumothorax. No pleural effusion. Lungs clear.'), [])
        f = rc.rule_scan('Spiculated mass in the right upper lobe.')
        self.assertEqual(f[0]['severity'], 'HIGH')

    def test_evaluation_combines_sources_and_ml_is_optional(self):
        o = self.order()
        rep = RadiologyReport(order_id=o.id, findings='Free intraperitoneal air under the diaphragm.',
                              impression='Pneumoperitoneum.', reported_by=self.rad.id, status='Draft')
        db.session.add(rep)
        db.session.commit()
        with mock.patch('app.services.radiology_critical.ml_scan', return_value=None):
            ev = rc.evaluate_report(o, rep)
        self.assertTrue(ev['critical'])
        self.assertEqual(ev['severity'], 'CRITICAL')
        self.assertTrue(ev['ai_assisted'])
        with mock.patch('app.services.radiology_critical.ml_scan',
                        return_value={'critical_finding': True, 'priority': 'CRITICAL',
                                      'finding_type': 'Perforation', 'confidence': 0.9}):
            ev = rc.evaluate_report(o, rep)
        self.assertIn('model', [f['source'] for f in ev['findings']])


class CriticalWorkflowTests(RadBase):
    def _enter_report(self, findings, impression, ml=None):
        o = self.order()
        self.login(self.rad.email)
        with mock.patch('app.services.radiology_critical.ml_scan', return_value=ml):
            r = self.client.post(f'/radiology/orders/{o.id}/report',
                                 data={'findings': findings, 'impression': impression,
                                       'recommendation': 'Clinical correlation'}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        return o

    def test_full_critical_lifecycle_with_audit(self):
        o = self._enter_report('Large left tension pneumothorax with mediastinal shift.',
                               'Tension pneumothorax.',
                               ml={'critical_finding': True, 'priority': 'CRITICAL',
                                   'finding_type': 'Pneumothorax', 'confidence': 0.74,
                                   'message': 'model flagged'})
        report = RadiologyReport.query.filter_by(order_id=o.id).first()
        # report untouched by AI
        self.assertEqual(report.impression, 'Tension pneumothorax.')
        # alert
        alert = ClinicalAlert.query.filter_by(source_type='radiology_report', source_id=report.id).one()
        self.assertEqual(alert.severity, 'CRITICAL')
        self.assertEqual(alert.status, 'OPEN')
        self.assertTrue(alert.ai_assisted)
        self.assertEqual(alert.assigned_to, self.doc.id)
        self.assertIn('rule', alert.rationale)
        # urgent task for the responsible physician
        task = Task.query.filter_by(related_resource_type='clinical_alert', related_resource_id=alert.id).one()
        self.assertEqual(task.priority, 'URGENT')
        self.assertEqual(task.assigned_to, self.doc.id)
        # immediate notification + structured communication record
        n = Notification.query.filter_by(user_id=self.doc.id, entity_type='clinical_alert',
                                         entity_id=alert.id).first()
        self.assertIsNotNone(n)
        self.assertIn('CRITICAL RADIOLOGY FINDING', n.title)
        self.assertEqual(CriticalFindingNotification.query.filter_by(order_id=o.id).count(), 1)
        # physician sees the banner on any page, not only in the notification centre
        self.login(self.doc.email)
        r = self.client.get('/doctor/dashboard')
        self.assertIn(b'CRITICAL FINDING', r.data)
        self.assertIn(f'/clinical/alerts/{alert.id}'.encode(), r.data)
        # acknowledge -> in progress -> resolve with documented action
        r = self.client.post(f'/clinical/alerts/{alert.id}', data={'action': 'acknowledge'}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        db.session.refresh(alert)
        self.assertEqual(alert.status, 'ACKNOWLEDGED')
        self.assertEqual(alert.acknowledged_by, self.doc.id)
        self.client.post(f'/clinical/alerts/{alert.id}', data={'action': 'start'})
        db.session.refresh(alert)
        self.assertEqual(alert.status, 'IN_PROGRESS')
        r = self.client.post(f'/clinical/alerts/{alert.id}', data={'action': 'resolve', 'action_taken': ''},
                             follow_redirects=True)
        db.session.refresh(alert)
        self.assertEqual(alert.status, 'IN_PROGRESS')        # documented action required
        self.assertIn(b'Document the action taken', r.data)
        self.client.post(f'/clinical/alerts/{alert.id}',
                         data={'action': 'resolve', 'action_taken': 'Chest drain inserted at bedside'})
        db.session.refresh(alert)
        self.assertEqual(alert.status, 'RESOLVED')
        self.assertEqual(alert.action_taken, 'Chest drain inserted at bedside')
        db.session.refresh(task)
        self.assertEqual(task.status, 'COMPLETED')
        actions = [a.action for a in AuditLog.query.filter_by(resource='clinical_alert', resource_id=alert.id).all()]
        for expected in ('CRITICAL_RADIOLOGY_ALERT', 'ALERT_ACK', 'ALERT_IN_PROGRESS', 'ALERT_RESOLVE'):
            self.assertIn(expected, actions)
        # banner gone
        r = self.client.get('/doctor/dashboard')
        self.assertNotIn(b'ACKNOWLEDGEMENT REQUIRED', r.data)
        # detail page renders receipts, tasks, audit
        r = self.client.get(f'/clinical/alerts/{alert.id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Who was notified', r.data)
        self.assertIn(b'Chest drain inserted', r.data)

    def test_non_critical_report_raises_nothing(self):
        o = self._enter_report('No pneumothorax. Heart size normal. Lungs clear.',
                               'Normal chest radiograph.',
                               ml={'critical_finding': False, 'priority': 'ROUTINE', 'confidence': 0.1})
        self.assertEqual(ClinicalAlert.query.count(), 0)
        self.assertEqual(Task.query.filter_by(task_type='CRITICAL_RESULT').count(), 0)
        self.assertEqual(Notification.query.filter_by(entity_type='clinical_alert').count(), 0)
        self.assertEqual(CriticalFindingNotification.query.filter_by(order_id=o.id).count(), 0)

    def test_signing_is_idempotent_and_dismissal_needs_reason(self):
        o = self._enter_report('Acute pulmonary embolism in the right main pulmonary artery.',
                               'Pulmonary embolism.', ml=None)
        alert = ClinicalAlert.query.one()
        self.login(self.rad.email)
        with mock.patch('app.services.radiology_critical.ml_scan', return_value=None):
            self.client.post(f'/radiology/orders/{o.id}/sign', follow_redirects=True)
        self.assertEqual(ClinicalAlert.query.count(), 1)
        self.assertEqual(Task.query.filter_by(related_resource_type='clinical_alert').count(), 1)
        self.login(self.doc.email)
        r = self.client.post(f'/clinical/alerts/{alert.id}', data={'action': 'dismiss', 'note': ''},
                             follow_redirects=True)
        db.session.refresh(alert)
        self.assertEqual(alert.status, 'OPEN')
        self.assertIn(b'reason is required', r.data)
        self.client.post(f'/clinical/alerts/{alert.id}', data={'action': 'dismiss', 'note': 'Known, treated PE on anticoagulation'})
        db.session.refresh(alert)
        self.assertEqual(alert.status, 'DISMISSED')

    def test_escalation_notifies_supervisor_after_threshold(self):
        self._enter_report('Subarachnoid haemorrhage.', 'SAH.', ml=None)
        alert = ClinicalAlert.query.one()
        alert.created_at = utcnow() - timedelta(minutes=45)
        db.session.commit()
        self.app.config['ALERT_ESCALATION_MINUTES'] = {'CRITICAL': 30, 'HIGH': 120}
        from app.services.preventive import run_preventive_sweep
        res = run_preventive_sweep()
        db.session.commit()
        self.assertEqual(res['escalated_alerts'], 1)
        self.assertTrue(Notification.query.filter_by(user_id=self.admin.id, entity_type='clinical_alert').count() >= 1)
        self.assertEqual(Task.query.filter_by(task_type='ESCALATION').count(), 1)
        # SuperAdmin/Admin sees unresolved critical alerts in the banner
        self.login(self.admin.email)
        r = self.client.get('/admin/dashboard')
        self.assertIn(b'CRITICAL FINDING', r.data)

    def test_radiology_ai_pages_render_for_physician_and_radiologist(self):
        o = self._enter_report('Displaced fracture of the distal radius.', 'Displaced fracture.', ml=None)
        for who in (self.doc, self.rad):
            self.login(who.email)
            r = self.client.get('/ai/copilot/radiology')
            self.assertEqual(r.status_code, 200)
            with mock.patch('app.services.radiology_critical.ml_scan', return_value=None):
                r = self.client.get(f'/ai/copilot/radiology/{o.id}')
            self.assertEqual(r.status_code, 200)
            self.assertIn(b'Radiologist Report', r.data)
            self.assertIn(b'AI Clinical Assistance', r.data)
            self.assertIn(b'Displaced fracture', r.data)
            self.assertIn(b'Why flagged', r.data)


if __name__ == '__main__':
    unittest.main()
