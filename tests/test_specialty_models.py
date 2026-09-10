"""The four imaging models follow one specialty policy everywhere: sidebar,
dashboard AI block, AI Hub and the routes themselves."""
import unittest

from app import create_app, db
from app.models import Doctor, Role, Specialty, User
from app.permissions import seed_permissions
from seed import ROLES


class Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.ctx = self.app.app_context(); self.ctx.push()
        db.create_all()
        for n in ROLES:
            db.session.add(Role(name=n))
        db.session.commit()
        seed_permissions(db)
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def _doctor(self, uname, specialty=None):
        u = User(username=uname, email=f'{uname}@t.com', full_name=uname.title(), user_type='doctor')
        u.set_password('123456'); u.roles.append(Role.query.filter_by(name='Doctor').first())
        db.session.add(u); db.session.flush()
        spec_id = None
        if specialty:
            sp = Specialty.query.filter_by(name=specialty).first() or Specialty(name=specialty)
            db.session.add(sp); db.session.flush(); spec_id = sp.id
        db.session.add(Doctor(user_id=u.id, specialty_id=spec_id)); db.session.commit()
        return u

    def _login(self, u):
        self.client.post('/auth/login', data={'email': u.email, 'password': '123456'})

    def _models_on(self, path):
        html = self.client.get(path).data.decode()
        return {k for k, url in (('chest', '/ai/chest-xray'), ('fracture', '/ai/fracture-detection'),
                                  ('tooth', '/ai/tooth-segmentation'), ('skin', '/ai/skin-lesion-detection'))
                if f'href="{url}"' in html}


class PolicyTests(Base):
    def test_policy_by_specialty(self):
        from app.services.ai.specialty_models import allowed_models
        derm = self._doctor('derm', 'Dermatology'); ortho = self._doctor('ortho', 'Orthopedics')
        im = self._doctor('im', 'Internal Medicine'); gen = self._doctor('gen', 'General'); none = self._doctor('none')
        self.assertEqual(allowed_models(derm), {'skin'})
        self.assertEqual(allowed_models(ortho), {'fracture'})
        self.assertEqual(allowed_models(im), {'chest'})
        self.assertEqual(allowed_models(gen), {'chest', 'fracture', 'skin'})     # unknown specialty: full set
        self.assertEqual(allowed_models(none), {'chest', 'fracture', 'skin'})

    def test_dermatologist_sees_only_skin_everywhere(self):
        derm = self._doctor('derm', 'Dermatology'); self._login(derm)
        self.assertEqual(self._models_on('/doctor/dashboard'), {'skin'})   # sidebar + AI block
        self.assertEqual(self._models_on('/ai/hub'), {'skin'})
        r = self.client.get('/ai/chest-xray')
        self.assertEqual(r.status_code, 302); self.assertIn('/ai/hub', r.headers['Location'])
        self.assertEqual(self.client.get('/ai/fracture-detection').status_code, 302)
        self.assertEqual(self.client.get('/ai/skin-lesion-detection').status_code, 200)

    def test_internist_sees_only_chest(self):
        im = self._doctor('im', 'Internal Medicine'); self._login(im)
        self.assertEqual(self._models_on('/doctor/dashboard'), {'chest'})
        self.assertEqual(self.client.get('/ai/chest-xray').status_code, 200)
        self.assertEqual(self.client.get('/ai/skin-lesion-detection').status_code, 302)

    def test_admin_sees_all_four(self):
        u = User(username='adm', email='adm@t.com', full_name='Adm', user_type='admin'); u.set_password('123456')
        u.roles.append(Role.query.filter_by(name='Admin').first()); db.session.add(u); db.session.commit()
        self._login(u)
        self.assertEqual(self._models_on('/admin/dashboard'), {'chest', 'fracture', 'tooth', 'skin'})

    def test_landing_page_lists_the_four_models(self):
        html = self.client.get('/home').data.decode()
        for name in ('Chest X-ray Screening', 'Fracture Detection', 'Tooth Segmentation', 'Skin Lesion Detection'):
            self.assertIn(name, html)
        self.assertIn('landing-models', html)


if __name__ == '__main__':
    unittest.main()
