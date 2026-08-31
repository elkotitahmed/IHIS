"""Task-engine and notification integration tests (scenario A: task lifecycle)."""
import re
import unittest

from app import create_app, db
from app.models import User, Role, Patient, Specialty, Doctor, Task, TaskActivity


def _csrf(html):
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1).decode() if m else ''


class TaskEngineTestCase(unittest.TestCase):
    ROLES = ['SuperAdmin', 'Admin', 'Doctor', 'Nurse', 'Patient',
             'LabTechnician', 'Radiologist', 'Pharmacist', 'Receptionist',
             'Dentist', 'Physiotherapist']

    def setUp(self):
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for name in self.ROLES:
            db.session.add(Role(name=name))
        db.session.commit()
        from app.permissions import seed_permissions
        seed_permissions(db)
        self.admin = self._make_user('admin@t.com', 'admin', 'Admin')
        self.doc = self._make_user('doc@t.com', 'doctor', 'Doctor')
        self.lab = self._make_user('lab@t.com', 'lab', 'LabTechnician')
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

    def _login(self, email, password='123456'):
        self.client.get('/auth/logout')
        page = self.client.get('/auth/login')
        tok = _csrf(page.data)
        return self.client.post('/auth/login', data={
            'email': email, 'password': password, 'csrf_token': tok,
        }, follow_redirects=True)

    def _create_task(self, as_email, title='Collect sample'):
        """Create a task assigned to the lab tech via the route."""
        self._login(as_email)
        r = self.client.get('/tasks/task/new')
        token = _csrf(r.data)
        r = self.client.post('/tasks/task/new', data={
            'title': title, 'description': 'Do it', 'task_type': 'LAB',
            'department': 'Laboratory', 'priority': 'HIGH',
            'assignee_id': self.lab.id, 'csrf_token': token,
        }, follow_redirects=True)
        return r

    def test_create_task_and_detail(self):
        r = self._create_task(self.admin.email)
        self.assertEqual(r.status_code, 200)
        task = Task.query.filter_by(title='Collect sample').first()
        self.assertIsNotNone(task)
        self.assertEqual(task.status, 'NEW')
        self.assertEqual(task.assigned_to, self.lab.id)
        # activity audit row written
        self.assertTrue(TaskActivity.query.filter_by(task_id=task.id,
                                                     action='CREATED').first())
        # assignee notification created
        from app.models import Notification
        n = Notification.query.filter_by(user_id=self.lab.id,
                                          entity_type='task',
                                          entity_id=task.id).first()
        self.assertIsNotNone(n)

    def test_transition_lifecycle(self):
        self._create_task(self.admin.email)
        task = Task.query.filter_by(title='Collect sample').first()
        from app.services import tasks as svc
        svc.transition(task, 'IN_PROGRESS')
        svc.transition(task, 'COMPLETED')
        db.session.commit()
        self.assertEqual(task.status, 'COMPLETED')
        self.assertIsNotNone(task.completed_at)
        self.assertIsNotNone(task.started_at)
        acts = [a.action for a in TaskActivity.query.filter_by(task_id=task.id)]
        self.assertIn('TRANSITION', acts)

    def test_illegal_transition_rejected(self):
        self._create_task(self.admin.email)
        task = Task.query.filter_by(title='Collect sample').first()
        from app.services import tasks as svc
        with self.assertRaises(ValueError):
            # COMPLETED is not reachable directly from NEW
            svc.transition(task, 'COMPLETED')
        db.session.rollback()
        self.assertEqual(task.status, 'NEW')

    def test_my_tasks_route_renders(self):
        self._create_task(self.admin.email)
        task = Task.query.filter_by(title='Collect sample').first()
        task.assigned_to = self.lab.id
        db.session.commit()
        self._login(self.lab.email)
        r = self.client.get('/tasks/my-tasks')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Collect sample', r.data)

    def test_queue_route_renders(self):
        self._create_task(self.admin.email, title='Queue task')
        self._login(self.admin.email)
        r = self.client.get('/tasks/queue')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Queue task', r.data)

    def test_route_transition_post(self):
        self._create_task(self.admin.email)
        task = Task.query.filter_by(title='Collect sample').first()
        task.assigned_to = self.lab.id
        db.session.commit()
        self._login(self.lab.email)
        r = self.client.get(f'/tasks/tasks/{task.id}')
        token = _csrf(r.data)
        r = self.client.post(f'/tasks/tasks/{task.id}/transition', data={
            'status': 'IN_PROGRESS', 'note': 'started', 'csrf_token': token,
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        task = db.session.get(Task, task.id)
        self.assertEqual(task.status, 'IN_PROGRESS')


if __name__ == '__main__':
    unittest.main()
