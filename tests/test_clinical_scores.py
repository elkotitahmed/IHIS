"""NEWS2 / qSOFA / LACE, the DDInter reference and the full ICD-10-CM index."""
import unittest
from types import SimpleNamespace as NS

from app.services import clinical_scores as cs
from app.services import drug_interactions as ddi
from app.services import terminology


class NEWS2Tests(unittest.TestCase):
    def test_normal_vitals_score_zero(self):
        v = NS(respiratory_rate=16, oxygen_saturation=97, blood_pressure_systolic=120, heart_rate=72, temperature=36.8)
        r = cs.news2(v)
        self.assertEqual(r['score'], 0)
        self.assertEqual(r['band'], 'LOW')
        self.assertFalse(r['partial'])

    def test_deteriorating_patient_is_high(self):
        v = NS(respiratory_rate=26, oxygen_saturation=90, blood_pressure_systolic=88, heart_rate=125, temperature=39.4)
        r = cs.news2(v, on_oxygen=True)
        self.assertEqual(r['score'], 3 + 3 + 2 + 3 + 2 + 2)
        self.assertEqual(r['band'], 'HIGH')

    def test_single_extreme_parameter_is_low_medium(self):
        v = NS(respiratory_rate=16, oxygen_saturation=97, blood_pressure_systolic=120, heart_rate=38, temperature=36.8)
        self.assertEqual(cs.news2(v)['band'], 'LOW-MEDIUM')

    def test_consciousness_scores_three(self):
        v = NS(respiratory_rate=16, oxygen_saturation=97, blood_pressure_systolic=120, heart_rate=72, temperature=36.8)
        self.assertEqual(cs.news2(v, consciousness='V')['components']['consciousness'], 3)

    def test_missing_inputs_are_reported_not_guessed(self):
        v = NS(respiratory_rate=None, oxygen_saturation=97, blood_pressure_systolic=120, heart_rate=72, temperature=None)
        r = cs.news2(v)
        self.assertTrue(r['partial'])
        self.assertIn('respiratory_rate', r['missing'])
        self.assertIn('temperature', r['missing'])


class QSOFATests(unittest.TestCase):
    def test_two_points_positive(self):
        r = cs.qsofa(NS(respiratory_rate=24, blood_pressure_systolic=95))
        self.assertEqual(r['score'], 2)
        self.assertTrue(r['positive'])

    def test_one_point_negative(self):
        r = cs.qsofa(NS(respiratory_rate=24, blood_pressure_systolic=130))
        self.assertFalse(r['positive'])


class LACETests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(cs.lace(0, False, 0, 0)['risk'], 'LOW')
        r = cs.lace(10, True, 4, 3)
        self.assertEqual(r['score'], 5 + 3 + 4 + 3)
        self.assertEqual(r['risk'], 'HIGH')

    def test_comorbidity_points_from_text(self):
        self.assertEqual(cs.comorbidity_points('Type 2 diabetes mellitus; essential hypertension'), 1)
        self.assertGreaterEqual(cs.comorbidity_points('Metastatic colon cancer'), 5)


class DDInterTests(unittest.TestCase):
    def test_reference_loaded(self):
        self.assertTrue(ddi.available())
        self.assertGreater(ddi.size(), 100000)

    def test_known_major_pair(self):
        rows = ddi.check(['Warfarin 5 mg', 'Aspirin 100 mg'])
        self.assertTrue(rows, 'warfarin + aspirin must be documented')
        self.assertEqual(rows[0]['source'], 'DDInter')

    def test_no_self_or_unknown_pairs(self):
        self.assertEqual(ddi.check(['Metformin', 'Metformin']), [])
        self.assertEqual(ddi.check(['Not-a-drug-xyz', 'Metformin']), [])


class ICDFullListTests(unittest.TestCase):
    def test_full_list_loaded(self):
        self.assertGreater(terminology.icd_count(), 70000)

    def test_curated_first_then_full(self):
        out = terminology.search_icd('hyper')
        self.assertEqual(out[0]['code'], 'I10')

    def test_rare_code_found_in_full_list(self):
        out = terminology.search_icd('cholera')
        self.assertTrue(any(r['code'].startswith('A00') for r in out))

    def test_code_prefix_search(self):
        out = terminology.search_icd('J18')
        self.assertTrue(any(r['code'] == 'J18.9' for r in out))


if __name__ == '__main__':
    unittest.main()
