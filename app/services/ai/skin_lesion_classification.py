"""AI Skin Lesion Classification service — ResNet-50 + EfficientNet-B0.

Ports the standalone "Comparative Study ResNet vs EfficientNet" skin-cancer
project (HAM10000, binary Nevus/Melanoma) into iHIS as lazy-loaded PyTorch
singletons so checkpoints are only read from disk on the first request.

Two checkpoints are supported and are *ensembled* (averaged softmax):

* ``resnet50_best.pth``       — timm ResNet-50, 224x224, ImageNet norm.
* ``efficientnet_b0_best.pth`` — timm EfficientNet-B0, 224x224, ImageNet norm.

Labels: 0 = Nevus (benign), 1 = Melanoma (malignant).

Also generates a Grad-CAM attention heatmap for the most confident model so
clinicians can see what the model looked at. Falls back gracefully when a
checkpoint (or PyTorch/timm) is unavailable; the feature still works with a
single installed model.
"""
import io
import os
import threading
import uuid

import numpy as np
import werkzeug

from PIL import Image
from flask import current_app

MODEL_NAMES = ('resnet50_best.pth', 'efficientnet_b0_best.pth')
ARCHS = {'resnet50_best.pth': 'resnet50',
         'efficientnet_b0_best.pth': 'efficientnet_b0'}
CLASS_NAMES = {0: 'Nevus', 1: 'Melanoma'}
IMG_SIZE = (224, 224)
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_lock = threading.Lock()
_loaded = False
_models = {}  # checkpoint filename -> (timm model, device)


def _model_dir():
    return os.path.join(current_app.static_folder, 'ai_models')


def skin_model_available():
    """True when at least one supported checkpoint is installed."""
    return any(os.path.exists(os.path.join(_model_dir(), n))
               for n in MODEL_NAMES)


def installed_models():
    """Return the list of checkpoint filenames actually installed."""
    return [n for n in MODEL_NAMES
            if os.path.exists(os.path.join(_model_dir(), n))]


def _upload_dir():
    # Private directory for input lesion images. Never under static/, so raw
    # PHI dermatology photos are not publicly served.
    base = current_app.config.get('UPLOAD_FOLDER') or 'var/uploads'
    return os.path.join(base, 'ai', 'skin', 'uploads')


def _result_dir():
    # Private directory for generated Grad-CAM heatmaps.
    base = current_app.config.get('UPLOAD_FOLDER') or 'var/uploads'
    return os.path.join(base, 'ai', 'skin', 'results')


def safe_filename(filename):
    try:
        return werkzeug.utils.secure_filename(filename)
    except Exception:
        return f'{uuid.uuid4().hex}.jpg'


def _load_one(name):
    """Load a single checkpoint as a timm model (device-aware)."""
    import timm
    import torch

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    path = os.path.join(_model_dir(), name)
    if not os.path.exists(path):
        raise FileNotFoundError(f'Skin lesion model not found: {path}')
    arch = ARCHS[name]
    model = timm.create_model(arch, pretrained=False, num_classes=2)
    state = torch.load(path, map_location=device)
    # Accept both a full state_dict and a {"state_dict": ...} wrapper.
    if isinstance(state, dict) and 'state_dict' in state:
        state = state['state_dict']
    model.load_state_dict(state)
    model = model.to(device)
    model.eval()
    return model, device


def _get_models():
    """Lazy-load all installed checkpoints once (thread-safe)."""
    global _loaded, _models
    if _loaded:
        return _models
    with _lock:
        if _loaded:
            return _models
        for name in installed_models():
            _models[name] = _load_one(name)
        if not _models:
            raise RuntimeError('No skin lesion model is installed on this server.')
        _loaded = True
        return _models


class _GradCAM:
    """Minimal Grad-CAM (no external dependency).

    Hooks the deepest convolutional layer, captures its activations and the
    gradients of the target class w.r.t. those activations, then produces the
    standard weighted activation map.
    """

    def __init__(self, model):
        self.model = model
        self.target = self._find_target_layer()
        self.activations = None
        self.gradients = None
        self._fwd = None
        self._bwd = None

    def _find_target_layer(self):
        import torch.nn as nn
        from torch.nn.modules.conv import _ConvNd
        for module in reversed(list(self.model.modules())):
            if isinstance(module, _ConvNd):
                return module
        raise RuntimeError('No convolutional layer found for Grad-CAM.')

    def _attach(self):
        import torch
        if self._fwd is not None:
            return
        def _fwd_hook(module, inp, out):
            self.activations = out.detach()
        def _bwd_hook(module, grad_in, grad_out):
            self.gradients = grad_out[0].detach()
        self._fwd = self.target.register_forward_hook(_fwd_hook)
        self._bwd = self.target.register_full_backward_hook(_bwd_hook)

    def _detach(self):
        if self._fwd is not None:
            self._fwd.remove()
            self._bwd.remove()
            self._fwd = None
            self._bwd = None

    def generate(self, input_tensor, class_idx):
        import torch
        import torch.nn.functional as F
        import cv2
        self._attach()
        self.activations = None
        self.gradients = None
        input_tensor = input_tensor.clone().requires_grad_(True)
        output = self.model(input_tensor)
        score = output[0, class_idx]
        self.model.zero_grad()
        score.backward()
        grads = self.gradients
        acts = self.activations
        self._detach()
        if grads is None or acts is None:
            raise RuntimeError('Grad-CAM hooks produced no gradient signal.')
        weights = grads.mean(dim=(2, 3), keepdim=True)
        cam = torch.relu((weights * acts).sum(dim=1, keepdim=True))
        cam = F.interpolate(cam, size=IMG_SIZE, mode='bilinear',
                            align_corners=False)
        cam = cam[0, 0].cpu().numpy()
        cam = (cam - cam.min()) / (cam.max() - cam.min() + 1e-8)
        heatmap = cv2.applyColorMap(
            np.uint8(255 * cam), cv2.COLORMAP_JET)
        heatmap = cv2.cvtColor(heatmap, cv2.COLOR_BGR2RGB)
        return heatmap, cam


def _preprocess(img_bytes):
    image = Image.open(io.BytesIO(img_bytes)).convert('RGB')
    image = image.resize(IMG_SIZE)
    arr = np.asarray(image, dtype=np.float32) / 255.0
    arr = (arr - MEAN) / STD
    arr = arr.transpose((2, 0, 1))
    return image, arr[np.newaxis]


def _run_model(model, device, input_tensor):
    import torch
    input_tensor = torch.from_numpy(input_tensor)
    input_tensor = input_tensor.to(device)
    with torch.no_grad():
        output = model(input_tensor)
        probs = torch.softmax(output, dim=1)[0]
    return output, probs


def classify_skin_lesion(upload_file):
    """Classify an uploaded skin-lesion image as Nevus or Melanoma.

    Runs every installed checkpoint and ensembles the softmax probabilities.
    Returns a dict with the predicted label, confidence, per-model details,
    the private original-image key and (when Grad-CAM succeeds) a heatmap key.
    """
    os.makedirs(_upload_dir(), exist_ok=True)
    os.makedirs(_result_dir(), exist_ok=True)

    name = safe_filename(upload_file.filename)
    if not name.lower().endswith(('.png', '.jpg', '.jpeg')):
        return {'error': 'File type not allowed. Use .png, .jpg, or .jpeg'}

    if not skin_model_available():
        return {'error': 'Skin lesion model is not installed on this server.'}

    try:
        stream = upload_file.stream
        if hasattr(stream, 'seek'):
            stream.seek(0)
        img_bytes = stream.read()

        base = uuid.uuid4().hex
        ext = os.path.splitext(name)[1] or '.jpg'
        stored_name = base + ext
        upload_path = os.path.join(_upload_dir(), stored_name)
        original, input_arr = _preprocess(img_bytes)
        original.save(upload_path)

        models = _get_models()
        prob_sum = np.zeros(2, dtype=np.float64)
        per_model = []
        best = None  # (name, model, device, output, probs) of most confident
        for ckpt, (model, device) in models.items():
            output, probs = _run_model(model, device, input_arr)
            p = probs.cpu().numpy().astype(np.float64)
            prob_sum += p
            entry = {
                'model': ckpt.replace('_best.pth', '').replace('_', ' ').title(),
                'checkpoint': ckpt,
                'prediction': CLASS_NAMES[int(p.argmax())],
                'proba_melanoma': round(float(p[1]), 4),
                'proba_nevus': round(float(p[0]), 4),
                'confidence': round(float(p.max()), 4),
            }
            per_model.append(entry)
            if best is None or p.max() > best[3].max():
                best = (ckpt, model, device, output, probs)

        ensemble = prob_sum / len(models)
        pred_idx = int(np.argmax(ensemble))
        label = CLASS_NAMES[pred_idx]
        confidence = float(ensemble[pred_idx])

        result = {
            'feature': 'skin',
            'orig_key': stored_name,
            'prediction': label,
            'label': label,
            'is_melanoma': pred_idx == 1,
            'proba_melanoma': round(float(ensemble[1]), 4),
            'proba_nevus': round(float(ensemble[0]), 4),
            'confidence': confidence,
            'percent': int(confidence * 100),
            'per_model': per_model,
            'models_used': [m['checkpoint'] for m in per_model],
        }

        # Grad-CAM on the most confident model for the predicted class.
        try:
            import torch
            cam = _GradCAM(best[1])
            input_tensor = torch.from_numpy(input_arr).float().to(best[2])
            heatmap, _ = cam.generate(input_tensor, pred_idx)
            vis = np.uint8(0.45 * np.array(original) + 0.55 * heatmap)
            heatmap_name = f'heatmap_{base}.jpg'
            Image.fromarray(vis).save(os.path.join(_result_dir(), heatmap_name))
            result['heatmap_key'] = heatmap_name
            result['heatmap_model'] = best[0]
        except Exception:
            # Heatmap is a nice-to-have; prediction still succeeds without it.
            result['heatmap_key'] = None

        return result
    except FileNotFoundError as e:
        return {'error': str(e)}
    except RuntimeError as e:
        return {'error': str(e)}
    except Exception as e:  # noqa: BLE001
        return {'error': f'Processing failed: {e}'}