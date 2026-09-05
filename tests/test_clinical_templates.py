"""Tests for Clinical Templates (SOAP and other clinician note scaffolds)."""
import re
import unittest

from app import create_app, db
from app.models import User, Role, Specialty, Doctor, ClinicianTemplate


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class ClinicalTemplateTestCase(unittest.TestCase):
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
        self.spec = Specialty(name='Cardiology')
        db.session.add(self.spec)
        db.session.flush()
        self.doc = User(username='doc', email='doc@t.com', full_name='Dr Test',
                        user_type='doctor')
        self.doc.set_password('123456')
        self.doc.roles.append(Role.query.filter_by(name='Doctor').first())
        db.session.add(self.doc)
        db.session.commit()
        db.session.add(Doctor(user_id=self.doc.id, specialty_id=self.spec.id,
                              license_number='LDOC'))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        self.client.get('/auth/logout')
        r = self.client.get('/auth/login')
        return self.client.post('/auth/login', data={
            'email': 'doc@t.com', 'password': '123456',
            'csrf_token': _csrf(r.data)}, follow_redirects=True)

    def test_list_empty_and_create(self):
        self._login()
        r = self.client.get('/clinical/templates')
        self.assertEqual(r.status_code, 200)

        form = self.client.get('/clinical/templates/new')
        self.assertEqual(form.status_code, 200)
        csrf = _csrf(form.data)
        r = self.client.post('/clinical/templates/new', data={
            'csrf_token': csrf,
            'title': 'Cardiac Consult SOAP',
            'template_type': 'SOAP',
            'description': 'Initial cardiology consultation',
            'specialty_id': str(self.spec.id),
            'allowed_roles': 'Doctor, Nurse',
            'section_key': ['s', 'o', 'a', 'p'],
            'section_label': ['Subjective', 'Objective', 'Assessment', 'Plan'],
            'section_placeholder': ['', '', '', ''],
        }, follow_redirects=True)
        self.assertIn(b'Template created', r.data)
        tpl = ClinicianTemplate.query.filter_by(title='Cardiac Consult SOAP').first()
        self.assertIsNotNone(tpl)
        self.assertEqual(len(tpl.section_list), 4)
        self.assertEqual(tpl.section_list[0]['key'], 's')

    def test_duplicate_title_rejected(self):
        self._login()
        form = self.client.get('/clinical/templates/new')
        csrf = _csrf(form.data)
        base = {'csrf_token': csrf, 'title': 'Standard SOAP',
                'template_type': 'SOAP',
                'section_key': ['s', 'o', 'a', 'p'],
                'section_label': ['Subjective', 'Objective', 'Assessment', 'Plan'],
                'section_placeholder': ['', '', '', '']}
        self.client.post('/clinical/templates/new', data=base, follow_redirects=True)
        form2 = self.client.get('/clinical/templates/new')
        base['csrf_token'] = _csrf(form2.data)
        r = self.client.post('/clinical/templates/new', data=base,
                             follow_redirects=True)
        self.assertIn(b'already exists', r.data)

    def test_edit_and_toggle(self):
        self._login()
        form = self.client.get('/clinical/templates/new')
        csrf = _csrf(form.data)
        self.client.post('/clinical/templates/new', data={
            'csrf_token': csrf, 'title': 'Discharge Template',
            'template_type': 'DISCHARGE',
            'section_key': ['summary', 'meds'],
            'section_label': ['Summary', 'Medications'],
            'section_placeholder': ['', ''],
        })
        tpl = ClinicianTemplate.query.filter_by(title='Discharge Template').first()
        self.assertIsNotNone(tpl)
        # toggle off
        edit = self.client.get(f'/clinical/templates/{tpl.id}/edit')
        self.assertEqual(edit.status_code, 200)
        self.client.post(f'/clinical/templates/{tpl.id}/toggle',
                         data={'csrf_token': _csrf(edit.data)},
                         follow_redirects=True)
        db.session.refresh(tpl)
        self.assertFalse(tpl.is_active)
        # toggle back on
        edit = self.client.get(f'/clinical/templates/{tpl.id}/edit')
        self.client.post(f'/clinical/templates/{tpl.id}/toggle',
                         data={'csrf_token': _csrf(edit.data)},
                         follow_redirects=True)
        db.session.refresh(tpl)
        self.assertTrue(tpl.is_active)

    def test_edit_updates_sections(self):
        self._login()
        form = self.client.get('/clinical/templates/new')
        csrf = _csrf(form.data)
        self.client.post('/clinical/templates/new', data={
            'csrf_token': csrf, 'title': 'Procedure Note',
            'template_type': 'PROCEDURE',
            'section_key': ['indication', 'findings'],
            'section_label': ['Indication', 'Findings'],
            'section_placeholder': ['', ''],
        })
        tpl = ClinicianTemplate.query.filter_by(title='Procedure Note').first()
        edit = self.client.get(f'/clinical/templates/{tpl.id}/edit')
        self.client.post(f'/clinical/templates/{tpl.id}/edit', data={
            'csrf_token': _csrf(edit.data), 'title': 'Procedure Note v2',
            'template_type': 'PROCEDURE',
            'section_key': ['indication', 'findings', 'complications'],
            'section_label': ['Indication', 'Findings', 'Complications'],
            'section_placeholder': ['', '', ''],
        }, follow_redirects=True)
        db.session.refresh(tpl)
        self.assertEqual(tpl.title, 'Procedure Note v2')
        self.assertEqual(len(tpl.section_list), 3)


if __name__ == '__main__':
    unittest.main()
