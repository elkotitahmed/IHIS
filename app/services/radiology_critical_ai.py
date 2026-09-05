"""Local integration for the Radiology Critical Results AI package."""

import importlib.util
from pathlib import Path

from flask import current_app


class RadiologyCriticalAI:
    """Load and run the supplied report classifier without a second server."""

    def __init__(self, package_dir=None):
        root = package_dir or current_app.config.get('RADIOLOGY_AI_PACKAGE_DIR')
        if not root:
            root = Path(current_app.root_path).parent / 'AI apps' / \
                'RadiologyAI_Integration_Package' / 'RadiologyAI_Integration_Package'
        self.package_dir = Path(root)
        self._detector = None

    def _load_detector(self):
        if self._detector is not None:
            return self._detector
        source = self.package_dir / 'critical_results_model_negation.py'
        model = self.package_dir / 'negation_model.pkl'
        vectorizer = self.package_dir / 'negation_vectorizer.pkl'
        if not source.is_file() or not model.is_file() or not vectorizer.is_file():
            raise FileNotFoundError('Radiology Critical Results AI model files are missing')

        spec = importlib.util.spec_from_file_location(
            'ihis_radiology_critical_model', source)
        if spec is None or spec.loader is None:
            raise ImportError('Unable to load the radiology AI model module')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        detector = module.NegationHandlingDetector()

        import joblib
        detector.model = joblib.load(model)
        detector.vectorizer = joblib.load(vectorizer)
        detector.is_trained = True
        self._detector = detector
        return detector

    def analyze(self, findings='', impression='', study_type=''):
        text = f'{findings or ""} {impression or ""}'.strip()
        result = self._load_detector().predict(text, threshold=0.5)
        result['study_type'] = study_type or ''
        return result
