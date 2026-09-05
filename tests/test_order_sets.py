"""Tests for Order Sets: create (with parallel item rows), validate, activate,
and apply to a patient (materialising real lab/imaging/prescription/referral
orders plus tasks and timeline events)."""
import re
import unittest

from app import create_app, db
from app.models import (User, Role, Patient, Specialty, Doctor, Department,
                        LabTestCatalog, ImagingType, Medication, CareTeam,
                        CareTeamMember, OrderSet)


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class OrderSetTestCase(unittest.TestCase):
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
        self.dept = Department(name='Medicine')
        db.session.add(self.dept)
        self.test = LabTestCatalog(test_name='CBC', category='Hematology',
                                   is_active=True)
        self.img = ImagingType(name='Chest X-Ray')
        self.med = Medication(generic_name='Paracetamol', is_active=True)
        db.session.add_all([self.test, self.img, self.med])
        db.session.flush()

        self.doc = self._make_user('doc@t.com', 'doctor', 'Doctor')
        self.doc_profile = Doctor.query.filter_by(user_id=self.doc.id).first()
        self.pat = self._make_patient('pat@t.com')
        # Need-to-know for the doctor.
        team = CareTeam(patient_id=self.pat.id, name='Team')
        db.session.add(team)
        db.session.flush()
        db.session.add(CareTeamMember(team_id=team.id, user_id=self.doc.id,
                                      role='Physician'))
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

    def _login(self, email):
        self.client.get('/auth/logout')
        r = self.client.get('/auth/login')
        return self.client.post('/auth/login', data={
            'email': email, 'password': '123456',
            'csrf_token': _csrf(r.data)}, follow_redirects=True)

    def _new_set_form(self):
        r = self.client.get('/clinical/order-sets/new')
        self.assertEqual(r.status_code, 200)
        return r, _csrf(r.data)

    def test_list_empty_and_create(self):
        self._login('doc@t.com')
        r = self.client.get('/clinical/order-sets')
        self.assertEqual(r.status_code, 200)

        page, csrf = self._new_set_form()
        r = self.client.post('/clinical/order-sets/new', data={
            'csrf_token': csrf,
            'name': 'Sepsis Bundle',
            'description': 'Standard sepsis workup',
            'category': 'Emergency',
            'specialty_id': str(self.spec.id),
            'department_id': str(self.dept.id),
            'item_type': ['LAB', 'RADIOLOGY', 'MEDICATION', 'REFERRAL'],
            'lab_test_id': [str(self.test.id), '', '', ''],
            'imaging_type_id': ['', str(self.img.id), '', ''],
            'medication_id': ['', '', str(self.med.id), ''],
            'dosage': ['', '', '1 g', ''],
            'frequency': ['', '', 'TID', ''],
            'duration': ['', '', '5 days', ''],
            'quantity': ['', '', '15', ''],
            'instructions': ['', '', 'with food', ''],
            'referral_specialty_id': ['', '', '', str(self.spec.id)],
            'priority': ['Normal', 'Normal', 'Normal', 'Normal'],
            'item_notes': ['FBC', 'PA view', '500mg x1g', 'to ID'],
        }, follow_redirects=True)
        self.assertIn(b'Order set created', r.data)
        oset = OrderSet.query.filter_by(name='Sepsis Bundle').first()
        self.assertIsNotNone(oset)
        self.assertEqual(len(oset.items), 4)
        labels = {it.item_type for it in oset.items}
        self.assertEqual(labels, {'LAB', 'RADIOLOGY', 'MEDICATION', 'REFERRAL'})

    def test_name_required_and_duplicate(self):
        self._login('doc@t.com')
        page, csrf = self._new_set_form()
        self.client.post('/clinical/order-sets/new', data={
            'csrf_token': csrf, 'name': 'Chest Path', 'item_type': ['LAB'],
            'lab_test_id': [str(self.test.id)],
            'imaging_type_id': [''], 'medication_id': [''],
            'referral_specialty_id': [''],
            'dosage': [''], 'frequency': [''], 'duration': [''],
            'quantity': [''], 'instructions': [''], 'priority': ['Normal'],
        })
        # Duplicate name -> rejected
        page, csrf2 = self._new_set_form()
        r = self.client.post('/clinical/order-sets/new', data={
            'csrf_token': csrf2, 'name': 'Chest Path',
            'item_type': ['LAB'], 'lab_test_id': [str(self.test.id)],
            'imaging_type_id': [''], 'medication_id': [''],
            'referral_specialty_id': [''],
            'dosage': [''], 'frequency': [''], 'duration': [''],
            'quantity': [''], 'instructions': [''], 'priority': ['Normal'],
        }, follow_redirects=True)
        self.assertIn(b'already exists', r.data)
        # Missing name -> required
        page, csrf3 = self._new_set_form()
        r = self.client.post('/clinical/order-sets/new', data={
            'csrf_token': csrf3, 'name': '  ',
            'item_type': ['LAB'], 'lab_test_id': [str(self.test.id)],
            'imaging_type_id': [''], 'medication_id': [''],
            'referral_specialty_id': [''],
            'dosage': [''], 'frequency': [''], 'duration': [''],
            'quantity': [''], 'instructions': [''], 'priority': ['Normal'],
        }, follow_redirects=True)
        self.assertIn(b'name is required', r.data)

    def test_empty_items_rejected(self):
        self._login('doc@t.com')
        page, csrf = self._new_set_form()
        r = self.client.post('/clinical/order-sets/new', data={
            'csrf_token': csrf, 'name': 'No Items Set',
            'item_type': ['LAB'], 'lab_test_id': [''],  # invalid -> dropped
            'imaging_type_id': [''], 'medication_id': [''],
            'referral_specialty_id': [''],
            'dosage': [''], 'frequency': [''], 'duration': [''],
            'quantity': [''], 'instructions': [''], 'priority': ['Normal'],
        }, follow_redirects=True)
        self.assertIn(b'at least one valid item', r.data)
        self.assertIsNone(OrderSet.query.filter_by(name='No Items Set').first())

    def test_apply_requires_active_and_materialises(self):
        self._login('doc@t.com')
        page, csrf = self._new_set_form()
        self.client.post('/clinical/order-sets/new', data={
            'csrf_token': csrf, 'name': 'Admission Panel',
            'item_type': ['LAB', 'MEDICATION'],
            'lab_test_id': [str(self.test.id), ''],
            'imaging_type_id': ['', ''],
            'medication_id': ['', str(self.med.id)],
            'dosage': ['', '500 mg'], 'frequency': ['', 'BID'],
            'duration': ['', '3 days'], 'quantity': ['', '6'],
            'instructions': ['', 'after meals'],
            'referral_specialty_id': ['', ''],
            'priority': ['Normal', 'Normal'],
        })
        oset = OrderSet.query.filter_by(name='Admission Panel').first()
        self.assertIsNotNone(oset)
        # Inactive sets cannot be applied.
        picker = self.client.get(
            f'/clinical/order-sets/{oset.id}/apply', follow_redirects=True)
        self.assertIn(b'inactive', picker.data)

        # Activate, then apply to the doctor's patient.
        page = self.client.get(f'/clinical/order-sets/{oset.id}')
        self.client.post(f'/clinical/order-sets/{oset.id}/toggle',
                         data={'csrf_token': _csrf(page.data)})
        apply_form = self.client.get(f'/clinical/order-sets/{oset.id}/apply')
        self.assertEqual(apply_form.status_code, 200)
        r = self.client.post(
            f'/clinical/order-sets/{oset.id}/apply/{self.pat.id}',
            data={'csrf_token': _csrf(apply_form.data)},
            follow_redirects=True)
        self.assertIn(b'Order set', r.data)

        from app.models import LabOrder, RadiologyOrder, Prescription, Referral
        self.assertEqual(LabOrder.query.filter_by(
            patient_id=self.pat.id).count(), 1)
        rx = Prescription.query.filter_by(patient_id=self.pat.id).first()
        self.assertIsNotNone(rx)
        self.assertEqual(len(rx.items), 1)
        # Tasks were generated from the order set (lab + pharmacy).
        from app.models import Task
        ttypes = {t.task_type for t in Task.query.filter_by(
            patient_id=self.pat.id).all()}
        self.assertTrue({'LAB', 'PHARMACY'}.issubset(ttypes))

    def test_apply_blocked_for_out_of_scope(self):
        """A doctor with no need-to-know cannot apply a set to an unknown patient."""
        other_pat = self._make_patient('other@t.com')

        self._login('doc@t.com')
        page, csrf = self._new_set_form()
        self.client.post('/clinical/order-sets/new', data={
            'csrf_token': csrf, 'name': 'Scope Panel',
            'item_type': ['LAB'], 'lab_test_id': [str(self.test.id)],
            'imaging_type_id': [''], 'medication_id': [''],
            'referral_specialty_id': [''],
            'dosage': [''], 'frequency': [''], 'duration': [''],
            'quantity': [''], 'instructions': [''], 'priority': ['Normal'],
        })
        oset = OrderSet.query.filter_by(name='Scope Panel').first()
        page2 = self.client.get(f'/clinical/order-sets/{oset.id}')
        self.client.post(f'/clinical/order-sets/{oset.id}/toggle',
                         data={'csrf_token': _csrf(page2.data)})
        apply_form = self.client.get(f'/clinical/order-sets/{oset.id}/apply')
        # Posting to a patient outside the doctor's scope should be blocked.
        r = self.client.post(
            f'/clinical/order-sets/{oset.id}/apply/{other_pat.id}',
            data={'csrf_token': _csrf(apply_form.data)}, follow_redirects=True)
        self.assertEqual(r.status_code, 403)


if __name__ == '__main__':
    unittest.main()
