"""AI Fracture Detection service — YOLOv8 bone-fracture detector.

Ports the standalone "Fracture Detection" project (fine-tuned YOLOv8-nano,
92.5% mAP@50) into iHIS as a lazy-loaded singleton so model weights are only
read from disk on the first request. Falls back gracefully when the model file
or ``ultralytics`` is unavailable.
"""
import os
import threading
import uuid
import werkzeug

from flask import current_app

MODEL_NAME = 'best.pt'
CLASS_NAMES = {0: 'Fracture', 1: 'Hand', 2: 'Hardware', 3: 'Hip', 4: 'Leg',
               5: 'Shoulder'}

_lock = threading.Lock()
_loaded = False
_model = None


def _upload_dir():
    return os.path.join(current_app.static_folder, 'ai_models', 'uploads')


def _result_dir():
    return os.path.join(current_app.static_folder, 'ai_models', 'results')


def fracture_model_available():
    """True when the YOLO model file exists."""
    path = os.path.join(current_app.static_folder, 'ai_models', MODEL_NAME)
    return os.path.exists(path)


def _get_model():
    """Lazy-load the YOLO model once (thread-safe)."""
    global _loaded, _model
    if _loaded:
        return _model
    with _lock:
        if _loaded:
            return _model
        path = os.path.join(current_app.static_folder, 'ai_models', MODEL_NAME)
        if not os.path.exists(path):
            raise FileNotFoundError(f'YOLO model not found: {path}')
        try:
            from ultralytics import YOLO
            _model = YOLO(path)
        except Exception as e:  # pragma: no cover - env-specific
            raise RuntimeError(f'Failed to load YOLO model: {e}')
        _loaded = True
        return _model


def safe_filename(filename):
    try:
        return werkzeug.utils.secure_filename(filename)
    except Exception:
        return f'{uuid.uuid4().hex}.jpg'


def detect_fracture(upload_file):
    """Run fracture detection on an uploaded image.

    Returns a dict with: original/annotated web URLs, the list of detections,
    an overall ``detected`` flag, counts by class and any error.
    """
    os.makedirs(_upload_dir(), exist_ok=True)
    os.makedirs(_result_dir(), exist_ok=True)

    name = safe_filename(upload_file.filename)
    if not name.lower().endswith(('.png', '.jpg', '.jpeg')):
        return {'error': 'File type not allowed. Use .png, .jpg, or .jpeg'}

    if not fracture_model_available():
        return {'error': 'Fracture detection model is not installed on this server.'}

    try:
        # unique names to avoid collisions between users
        base = uuid.uuid4().hex
        ext = os.path.splitext(name)[1] or '.jpg'
        stored_name = base + ext
        upload_path = os.path.join(_upload_dir(), stored_name)
        upload_file.save(upload_path)

        model = _get_model()
        results = model(upload_path)
        result = results[0]

        result_img_name = f'annotated_{base}.jpg'
        result_path = os.path.join(_result_dir(), result_img_name)
        result.save(result_path)

        detections = []
        total_conf = 0.0
        count_by_class = {}
        if len(result.boxes) > 0:
            for i in range(len(result.boxes)):
                conf = float(result.boxes.conf[i].item())
                cls = int(result.boxes.cls[i].item())
                label = CLASS_NAMES.get(cls, model.names.get(cls, str(cls)))
                detections.append({'label': label, 'confidence': round(conf, 3),
                                   'percent': int(conf * 100)})
                count_by_class[label] = count_by_class.get(label, 0) + 1
                total_conf += conf

        detected = len(detections) > 0
        avg_conf = int((total_conf / len(detections)) * 100) if detections else 0

        static_dir = 'ai_models'
        return {
            'detected': detected,
            'detections': detections,
            'count_by_class': count_by_class,
            'avg_confidence': avg_conf,
            'orig_url': '%s/uploads/%s' % (static_dir, stored_name),
            'result_url': '%s/results/%s' % (static_dir, result_img_name),
        }
    except FileNotFoundError as e:
        return {'error': str(e)}
    except RuntimeError as e:
        return {'error': str(e)}
    except Exception as e:  # noqa: BLE001
        return {'error': f'Processing failed: {e}'}
