"""Chest X-ray pathology screening (TorchXRayVision, MIT licence).

DenseNet-121 ``densenet121-res224-all`` (~30 MB), trained on CheXpert, NIH,
PadChest, MIMIC-CXR and RSNA. Returns a probability for 18 findings; runs on
CPU in about a second. Decision support only: a radiologist confirms.

Weights are downloaded once by the library into ``~/.torchxrayvision`` on
first use (internet needed that one time); ``chest_model_available()`` is
true when the library is importable, ``weights_ready()`` when the file exists.
"""
import os
import threading
import uuid

from flask import current_app

MODEL_NAME = 'densenet121-res224-all'
WEIGHTS_FILE = 'nih-pc-chex-mimic_ch-google-openi-kaggle-densenet121-d121-tw-lr001-rot45-tr15-sc15-seed0-best.pt'
WEIGHTS_URL = 'https://github.com/mlmed/torchxrayvision/releases/download/v1/' + WEIGHTS_FILE
WEIGHTS_MIN_BYTES = 20_000_000       # the real file is ~29 MB; anything smaller is a broken download
# Findings that should reach the critical-finding engine when the model is confident.
CRITICAL = {'Pneumothorax': 0.60, 'Effusion': 0.70, 'Pneumonia': 0.70, 'Consolidation': 0.70,
            'Edema': 0.70, 'Mass': 0.60, 'Fracture': 0.60}
FRIENDLY = {
    'Atelectasis': 'Atelectasis (collapsed lung tissue)', 'Consolidation': 'Consolidation',
    'Infiltration': 'Infiltration', 'Pneumothorax': 'Pneumothorax (air around the lung)',
    'Edema': 'Pulmonary oedema', 'Emphysema': 'Emphysema', 'Fibrosis': 'Fibrosis',
    'Effusion': 'Pleural effusion', 'Pneumonia': 'Pneumonia', 'Pleural_Thickening': 'Pleural thickening',
    'Cardiomegaly': 'Cardiomegaly (enlarged heart)', 'Nodule': 'Nodule', 'Mass': 'Mass',
    'Hernia': 'Hernia', 'Lung Lesion': 'Lung lesion', 'Fracture': 'Fracture',
    'Lung Opacity': 'Lung opacity', 'Enlarged Cardiomediastinum': 'Enlarged cardiomediastinum',
}

_lock = threading.Lock()
_model = None


def chest_model_available():
    try:
        import torchxrayvision  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _weights_path():
    return os.path.join(os.path.expanduser('~/.torchxrayvision/models_data'), WEIGHTS_FILE)


def weights_ready():
    p = _weights_path()
    return os.path.isfile(p) and os.path.getsize(p) >= WEIGHTS_MIN_BYTES


def ensure_weights():
    """Fetch the weights once (plain streaming download; the library's own
    progress bar breaks on non-UTF-8 consoles and leaves a truncated file)."""
    if weights_ready():
        return True
    import requests
    p = _weights_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.part'
    with requests.get(WEIGHTS_URL, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, 'wb') as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    if os.path.getsize(tmp) < WEIGHTS_MIN_BYTES:
        os.remove(tmp)
        raise RuntimeError('Chest X-ray weights download was incomplete.')
    os.replace(tmp, p)
    return True


def _get_model():
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is None:
            ensure_weights()
            import torchxrayvision as xrv
            m = xrv.models.DenseNet(weights=MODEL_NAME)
            m.eval()
            _model = m
    return _model


def _dirs():
    base = current_app.config.get('UPLOAD_FOLDER') or 'var/uploads'
    up = os.path.join(base, 'ai', 'chest', 'uploads')
    res = os.path.join(base, 'ai', 'chest', 'results')
    os.makedirs(up, exist_ok=True); os.makedirs(res, exist_ok=True)
    return up, res


def analyze_chest_xray(upload_file):
    """Return {'findings': [{'label','name','probability','percent','flag'}], 'top': [...],
    'critical': [...], 'orig_key', 'feature': 'chest'} or {'error': ...}."""
    if not chest_model_available():
        return {'error': 'Chest X-ray model library is not installed on this server.'}
    name = (upload_file.filename or '').lower()
    if not name.endswith(('.png', '.jpg', '.jpeg')):
        return {'error': 'File type not allowed. Use .png, .jpg, or .jpeg'}
    import numpy as np
    import torch
    import torchxrayvision as xrv
    from PIL import Image
    up, _ = _dirs()
    key = uuid.uuid4().hex + os.path.splitext(name)[1]
    path = os.path.join(up, key)
    upload_file.save(path)
    try:
        img = Image.open(path).convert('L')
        arr = np.asarray(img).astype('float32')
        arr = xrv.datasets.normalize(arr, 255)          # [-1024, 1024]
        arr = arr[None, ...]
        transform = xrv.datasets.XRayCenterCrop()
        arr = transform(arr)
        # resize to 224
        t = torch.from_numpy(arr)[None, ...]
        t = torch.nn.functional.interpolate(t, size=(224, 224), mode='bilinear', align_corners=False)
        model = _get_model()
        with torch.no_grad():
            probs = model(t)[0].numpy()
        findings = []
        for label, p in zip(model.pathologies, probs):
            if not label:
                continue
            p = float(p)
            thr = CRITICAL.get(label)
            findings.append({'label': label, 'name': FRIENDLY.get(label, label), 'probability': round(p, 4),
                             'percent': round(p * 100), 'flag': bool(thr and p >= thr)})
        findings.sort(key=lambda f: -f['probability'])
        critical = [f for f in findings if f['flag']]
        return {'findings': findings, 'top': findings[:5], 'critical': critical,
                'orig_key': key, 'feature': 'chest', 'model': MODEL_NAME,
                'note': 'Probabilities are model outputs on a 224×224 frontal view; not calibrated clinical risk.'}
    except Exception as e:  # noqa: BLE001
        return {'error': f'Processing failed: {e}'}
