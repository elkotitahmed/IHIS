"""Intracranial haemorrhage (ICH) detection on non-contrast head CT — local.

Stage 1: ConvNeXt-Tiny and Swin-Tiny slice classifiers (timm, ~107 MB each)
trained on three CT windows (brain 40/80, subdural 80/200, bone 600/2800)
stacked as RGB. LayerCAM fused over two stages shows what drove the call.
Stage 2 (optional): a bidirectional LSTM over ConvNeXt embeddings of a whole
series (``sequence_head.pt``). When that checkpoint is absent, a multi-slice
upload is scored per slice by the CNN and aggregated (mean of the three
highest slices) — the page says so.

Operating points come from the model author's held-out test set at 95 %
sensitivity: slice 0.103, study 0.072 (sequence output). They are deliberately
low: a *negative* is reassuring, a *positive* needs a radiologist.

Weights live in ``app/static/ai_models/`` (git-ignored). Uploads and heatmaps
are written under the private UPLOAD_FOLDER and served through ``ai_media``.
"""
import io
import os
import time
import uuid

from flask import current_app

SIZE = 224
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
BRAIN, SUBDURAL, BONE = (40, 80), (80, 200), (600, 2800)
THR_SLICE = 0.103
THR_STUDY = 0.072
SPECS = {
    'ConvNeXt-Tiny': ('convnext_tiny', 'convnext_tiny_best.pt'),
    'Swin-Tiny': ('swin_tiny_patch4_window7_224', 'swin_tiny_best.pt'),
}
SEQ_FILE = 'sequence_head.pt'
CAM_LAYERS = {'convnext_tiny': ['stages.1', 'stages.2'],
              'swin_tiny_patch4_window7_224': ['layers.1', 'layers.2']}
DICOM_EXTS = ('.dcm', '.dicom')
IMAGE_EXTS = ('.png', '.jpg', '.jpeg')

_models = {}
_seq = None
_loaded = False


def _model_dir():
    return os.path.join(current_app.static_folder, 'ai_models')


def installed_models():
    return [f for _, (_, f) in SPECS.items() if os.path.exists(os.path.join(_model_dir(), f))]


def ich_model_available():
    return bool(installed_models())


def sequence_model_available():
    return os.path.exists(os.path.join(_model_dir(), SEQ_FILE))


def _upload_dir():
    base = current_app.config.get('UPLOAD_FOLDER') or 'var/uploads'
    return os.path.join(base, 'ai', 'ich', 'uploads')


def _result_dir():
    base = current_app.config.get('UPLOAD_FOLDER') or 'var/uploads'
    return os.path.join(base, 'ai', 'ich', 'results')


# ---------------------------------------------------------------------------
# Preprocessing (must match training)
# ---------------------------------------------------------------------------
def _to_hu(dcm):
    import numpy as np
    if int(getattr(dcm, 'RescaleIntercept', 0)) > -100:
        x = dcm.pixel_array + 1000
        x[x >= 4096] = x[x >= 4096] - 4096
        dcm.PixelData = x.tobytes()
        dcm.RescaleIntercept = -1000
    return dcm.pixel_array * float(dcm.RescaleSlope) + float(dcm.RescaleIntercept)


def _window(hu, centre, width):
    import numpy as np
    lo, hi = centre - width / 2, centre + width / 2
    x = np.clip(hu, lo, hi)
    return ((x - lo) / (hi - lo) * 255).astype(np.uint8)


def read_dicom(raw):
    """(rgb uint8 224x224 with the three windows, z position, meta dict)."""
    import cv2
    import numpy as np
    import pydicom
    from pydicom import config as pdconfig
    pdconfig.settings.reading_validation_mode = pdconfig.IGNORE
    try:
        dcm = pydicom.dcmread(io.BytesIO(raw))
    except Exception:  # noqa: BLE001 - missing preamble / meta: force
        dcm = pydicom.dcmread(io.BytesIO(raw), force=True)
        if not getattr(getattr(dcm, 'file_meta', None), 'TransferSyntaxUID', None):
            dcm.file_meta = pydicom.dataset.FileMetaDataset()
            dcm.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian
    if not hasattr(dcm, 'RescaleIntercept'):
        dcm.RescaleIntercept = 0.0
    if not hasattr(dcm, 'RescaleSlope'):
        dcm.RescaleSlope = 1.0
    hu = _to_hu(dcm)
    rgb = np.stack([_window(hu, *BRAIN), _window(hu, *SUBDURAL), _window(hu, *BONE)], axis=-1)
    rgb = cv2.resize(rgb, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
    try:
        z = float(dcm.ImagePositionPatient[2])
    except Exception:  # noqa: BLE001
        z = float(getattr(dcm, 'InstanceNumber', 0) or 0)
    meta = {'modality': str(getattr(dcm, 'Modality', '') or ''),
            'series': str(getattr(dcm, 'SeriesDescription', '') or ''),
            'instance': int(getattr(dcm, 'InstanceNumber', 0) or 0)}
    return rgb, z, meta


def read_flat_image(raw):
    import cv2
    import numpy as np
    from PIL import Image
    g = np.array(Image.open(io.BytesIO(raw)).convert('L'))
    rgb = np.stack([g, g, g], axis=-1)
    return cv2.resize(rgb, (SIZE, SIZE), interpolation=cv2.INTER_AREA)


def _is_dicom(filename, raw):
    return (filename or '').lower().endswith(DICOM_EXTS) or raw[128:132] == b'DICM'


def _to_tensor(rgb):
    import numpy as np
    import torch
    x = (rgb.astype(np.float32) / 255.0 - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)
    return torch.from_numpy(x).permute(2, 0, 1)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
def _device():
    import torch
    return torch.device('cuda' if torch.cuda.is_available() else 'cpu')


def _load():
    global _loaded, _seq
    if _loaded:
        return
    import timm
    import torch
    for label, (arch, fname) in SPECS.items():
        path = os.path.join(_model_dir(), fname)
        if not os.path.exists(path):
            continue
        m = timm.create_model(arch, pretrained=False, num_classes=1)
        m.load_state_dict(torch.load(path, map_location='cpu'))
        _models[label] = m.eval().to(_device())
    seq_path = os.path.join(_model_dir(), SEQ_FILE)
    if os.path.exists(seq_path):
        import torch.nn as nn

        class SeqHead(nn.Module):
            def __init__(self, d_in=768, d=128, layers=2, drop=0.3):
                super().__init__()
                self.proj = nn.Sequential(nn.Linear(d_in, 256), nn.LayerNorm(256), nn.ReLU(), nn.Dropout(drop))
                self.rnn = nn.LSTM(256, d, num_layers=layers, batch_first=True, bidirectional=True, dropout=drop)
                self.out = nn.Linear(d * 2, 1)

            def forward(self, x):
                h = self.proj(x)
                h, _ = self.rnn(h)
                return self.out(h).squeeze(-1)

        s = SeqHead()
        s.load_state_dict(torch.load(seq_path, map_location='cpu'))
        _seq = s.eval().to(_device())
    _loaded = True


def _get_module(model, path):
    m = model
    for part in path.split('.'):
        m = m[int(part)] if part.isdigit() and not hasattr(m, part) else getattr(m, part)
    return m


def _to_nchw(t, c):
    if t.ndim == 4 and t.shape[1] != c and t.shape[-1] == c:
        return t.permute(0, 3, 1, 2)
    if t.ndim == 3:
        b, n, cc = t.shape
        s = int(round(n ** 0.5))
        return t.transpose(1, 2).reshape(b, cc, s, s)
    return t


def layer_cam(model, x, arch):
    """LayerCAM fused over two stages -> (cam 224x224 in [0,1], probability)."""
    import torch
    import torch.nn.functional as F
    acts, handles = {}, []
    for path in CAM_LAYERS[arch]:
        mod = _get_module(model, path)

        def fwd(m, i, o, p=path):
            acts[p] = o
            o.retain_grad()
        handles.append(mod.register_forward_hook(fwd))
    model.zero_grad(set_to_none=True)
    out = model(x)
    out.sum().backward()
    for h in handles:
        h.remove()
    c = model.num_features
    fused = None
    for path in CAM_LAYERS[arch]:
        a = _to_nchw(acts[path], c)
        g = _to_nchw(acts[path].grad, c)
        cam = torch.relu((torch.relu(g) * a).sum(1, keepdim=True))
        cam = F.interpolate(cam, (SIZE, SIZE), mode='bilinear', align_corners=False)[0, 0]
        rng = cam.max() - cam.min()
        cam = (cam - cam.min()) / rng if rng > 0 else torch.zeros_like(cam)
        fused = cam if fused is None else fused + cam
    fused = (fused / len(CAM_LAYERS[arch])).detach().cpu().numpy()
    rng = fused.max() - fused.min()
    if rng > 0:
        fused = (fused - fused.min()) / rng
    return fused, float(torch.sigmoid(out.detach().float())[0, 0])


def overlay(rgb, cam, pct=72):
    import cv2
    import numpy as np
    base = cv2.cvtColor(rgb[:, :, 0], cv2.COLOR_GRAY2BGR).astype(np.float32)
    thr = np.percentile(cam, pct)
    m = np.clip((cam - thr) / (1 - thr + 1e-8), 0, 1)
    heat = cv2.applyColorMap((cam * 255).astype(np.uint8), cv2.COLORMAP_JET).astype(np.float32)
    a = (m ** 0.75)[..., None] * 0.62
    blend = (heat * a + base * (1 - a)).astype(np.uint8)
    cy, cx = np.unravel_index(np.argmax(cam), cam.shape)
    cv2.circle(blend, (int(cx), int(cy)), 15, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.drawMarker(blend, (int(cx), int(cy)), (255, 255, 255), cv2.MARKER_CROSS, 11, 1, cv2.LINE_AA)
    return cv2.cvtColor(blend, cv2.COLOR_BGR2RGB), (int(cx), int(cy))


def _save_png(arr_rgb, name):
    import cv2
    os.makedirs(_result_dir(), exist_ok=True)
    big = cv2.resize(arr_rgb, (448, 448), interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(os.path.join(_result_dir(), name), cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
    return name


def _gray3(rgb, channel):
    import numpy as np
    return np.stack([rgb[:, :, channel]] * 3, axis=-1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def analyze_ich(uploads):
    """``uploads``: list of werkzeug FileStorage (1 image/DICOM = slice mode,
    2+ DICOMs = study mode). Returns a dict for the template; ``{'error': ...}``
    when nothing could be read."""
    import numpy as np
    import torch
    if not ich_model_available():
        return {'error': 'ICH model weights are not installed on this server.'}
    _load()
    if not _models:
        return {'error': 'ICH model weights could not be loaded.'}
    t0 = time.time()
    os.makedirs(_upload_dir(), exist_ok=True)
    base = uuid.uuid4().hex
    slices, skipped, flat = [], 0, False
    for f in uploads:
        raw = f.stream.read() if hasattr(f, 'stream') else f.read()
        name = getattr(f, 'filename', '') or ''
        try:
            if _is_dicom(name, raw):
                rgb, z, meta = read_dicom(raw)
            else:
                rgb, z, meta = read_flat_image(raw), float(len(slices)), {}
                flat = True
            slices.append((z, rgb, os.path.basename(name), meta))
        except Exception:  # noqa: BLE001 - unreadable file
            skipped += 1
    if not slices:
        return {'error': 'None of the uploaded files could be read as a CT slice (DICOM, PNG or JPG).'}
    slices.sort(key=lambda s: s[0])
    device = _device()
    note = ('Not a DICOM: the brain, subdural and bone windows cannot be reconstructed from a flat image, so one '
            'grayscale was copied into all three channels. Prediction and heatmap are less reliable.') if flat else ''

    # ---- slice mode ---------------------------------------------------------
    if len(slices) == 1:
        z, rgb, name, meta = slices[0]
        x = _to_tensor(rgb).unsqueeze(0).to(device)
        per_model, cams = [], []
        for label, model in _models.items():
            cam, prob = layer_cam(model, x.clone(), SPECS[label][0])
            img, peak = overlay(rgb, cam)
            key = _save_png(img, f'cam_{label.split("-")[0].lower()}_{base}.png')
            per_model.append({'model': label, 'probability': round(prob, 4), 'percent': round(prob * 100, 1),
                              'positive': prob > THR_SLICE, 'peak': peak, 'cam_key': key})
        probs = [m['probability'] for m in per_model]
        p = float(np.mean(probs))
        orig = _save_png(_gray3(rgb, 0), f'brain_{base}.png')
        windows = {'brain': orig, 'subdural': _save_png(_gray3(rgb, 1), f'subdural_{base}.png'),
                   'bone': _save_png(_gray3(rgb, 2), f'bone_{base}.png')}
        return {
            'feature': 'ich', 'mode': 'slice', 'orig_key': orig, 'windows': windows,
            'heatmap_key': per_model[0]['cam_key'], 'heatmap_model': per_model[0]['model'],
            'per_model': per_model, 'probability': round(p, 4), 'percent': round(p * 100, 1),
            'positive': p > THR_SLICE, 'agree': len({m['positive'] for m in per_model}) == 1,
            'threshold': THR_SLICE, 'flat_image': flat, 'note': note, 'meta': meta,
            'models_used': list(_models.keys()), 'sequence_model': _seq is not None,
            'n_slices': 1, 'seconds': round(time.time() - t0, 1),
        }

    # ---- study mode ---------------------------------------------------------
    cnn_label = 'ConvNeXt-Tiny' if 'ConvNeXt-Tiny' in _models else next(iter(_models))
    cnn = _models[cnn_label]
    rgbs = [s[1] for s in slices]
    feats, probs = [], []
    with torch.no_grad():
        for i in range(0, len(rgbs), 16):
            xb = torch.stack([_to_tensor(r) for r in rgbs[i:i + 16]]).to(device)
            f = cnn.forward_features(xb)
            pooled = cnn.forward_head(f, pre_logits=True)
            logit = cnn.head.fc(pooled)
            feats.append(pooled.float().cpu())
            probs.append(torch.sigmoid(logit.float()).cpu().squeeze(1))
    cnn_probs = torch.cat(probs).numpy()
    seq_probs = None
    if _seq is not None and len(slices) >= 8:
        with torch.no_grad():
            seq_probs = torch.sigmoid(_seq(torch.cat(feats).unsqueeze(0).to(device))).cpu().numpy()[0]
    scored = seq_probs if seq_probs is not None else cnn_probs
    threshold = THR_STUDY if seq_probs is not None else THR_SLICE
    study_score = float(np.sort(scored)[-3:].mean()) if len(scored) >= 3 else float(scored.max())
    order = sorted(np.argsort(-scored)[:6].tolist())
    gallery = []
    for i in order:
        x = _to_tensor(rgbs[i]).unsqueeze(0).to(device)
        cam, _ = layer_cam(cnn, x, SPECS[cnn_label][0])
        img, _ = overlay(rgbs[i], cam)
        gallery.append({'index': i + 1, 'z': round(slices[i][0], 1), 'name': slices[i][2],
                        'cnn': round(float(cnn_probs[i]) * 100, 1),
                        'seq': round(float(seq_probs[i]) * 100, 1) if seq_probs is not None else None,
                        'cam_key': _save_png(img, f'cam_s{i + 1}_{base}.png'),
                        'orig_key': _save_png(_gray3(rgbs[i], 0), f'brain_s{i + 1}_{base}.png'),
                        'flagged': bool(scored[i] > threshold)})
    top = int(np.argmax(scored))
    return {
        'feature': 'ich', 'mode': 'study', 'orig_key': gallery[0]['orig_key'] if gallery else None,
        'heatmap_key': None, 'per_model': [], 'gallery': gallery,
        'probability': round(study_score, 4), 'percent': round(study_score * 100, 1),
        'positive': study_score > threshold, 'threshold': threshold,
        'n_slices': len(slices), 'skipped': skipped, 'top_slice': top + 1,
        'n_flagged': int((scored > threshold).sum()),
        'curve': [round(float(v), 4) for v in scored],
        'cnn_curve': [round(float(v), 4) for v in cnn_probs],
        'z_range': [round(slices[0][0], 1), round(slices[-1][0], 1)],
        'sequence_model': seq_probs is not None, 'flat_image': flat, 'note': note,
        'models_used': [cnn_label] + (['sequence head'] if seq_probs is not None else []),
        'seconds': round(time.time() - t0, 1), 'meta': slices[0][3],
    }
