"""AI Tooth Segmentation service — U-Net dental segmentation.

Ports the standalone "Tooth Segmentation" project (U-Net, ~7.8M params, trained
on TUFTS panoramic radiographs) into iHIS. The heavy TensorFlow model is
lazy-loaded once. Preprocessing matches training exactly (grayscale -> CLAHE ->
256x256 -> normalize). Falls back gracefully when TensorFlow or the model file
is unavailable.
"""
import io
import os
import threading
import uuid
import numpy as np
import werkzeug

from flask import current_app

MODEL_NAME = 'dental_unet_model.keras'
IMG_SIZE = (256, 256)

_lock = threading.Lock()
_loaded = False
_model = None


def tooth_model_available():
    path = os.path.join(current_app.static_folder, 'ai_models', MODEL_NAME)
    return os.path.exists(path)


def _upload_dir():
    return os.path.join(current_app.static_folder, 'ai_models', 'uploads')


def _get_model():
    """Lazy-load the U-Net Keras model once (thread-safe)."""
    global _loaded, _model
    if _loaded:
        return _model
    with _lock:
        if _loaded:
            return _model
        path = os.path.join(current_app.static_folder, 'ai_models', MODEL_NAME)
        if not os.path.exists(path):
            raise FileNotFoundError(f'U-Net model not found: {path}')
        try:
            import tensorflow as tf
            from tensorflow.keras import backend as K
        except Exception as e:  # pragma: no cover - env-specific
            raise RuntimeError(f'TensorFlow not available: {e}')

        def dice_coef(y_true, y_pred, smooth=1e-6):
            y_true_f = K.flatten(y_true)
            y_pred_f = K.flatten(y_pred)
            intersection = K.sum(y_true_f * y_pred_f)
            return (2. * intersection + smooth) / (
                K.sum(y_true_f) + K.sum(y_pred_f) + smooth)

        def dice_loss(y_true, y_pred, smooth=1e-6):
            return 1 - dice_coef(y_true, y_pred, smooth)

        def bce_dice_loss(y_true, y_pred):
            bce = tf.keras.losses.binary_crossentropy(y_true, y_pred)
            return tf.reduce_mean(bce) + dice_loss(y_true, y_pred)

        try:
            _model = tf.keras.models.load_model(
                path,
                custom_objects={'dice_loss': dice_loss, 'dice_coef': dice_coef,
                                'bce_dice_loss': bce_dice_loss})
        except Exception as e:  # pragma: no cover - env-specific
            raise RuntimeError(f'Failed to load U-Net model: {e}')
        _loaded = True
        return _model


def safe_filename(filename):
    try:
        return werkzeug.utils.secure_filename(filename)
    except Exception:
        return f'{uuid.uuid4().hex}.jpg'


def preprocess(image_bytes):
    """Grayscale -> CLAHE -> resize 256x256 -> normalize -> (1,256,256,1)."""
    import cv2
    from PIL import Image
    image = Image.open(io.BytesIO(image_bytes)).convert('L')
    image = np.array(image)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    image = clahe.apply(image)
    image = cv2.resize(image, IMG_SIZE) / 255.0
    image = np.expand_dims(image, axis=0)
    image = np.expand_dims(image, axis=-1)
    return image


def postprocess(mask):
    """Threshold the model output to a displayable binary mask (0 or 255)."""
    mask = mask.squeeze()
    return (mask > 0.5).astype(np.uint8) * 255


def segment_tooth(upload_file):
    """Run tooth segmentation on an uploaded panoramic X-ray.

    Returns a dict with original/mask web URLs, estimated tooth coverage
    percentage, and any error.
    """
    from PIL import Image
    os.makedirs(_upload_dir(), exist_ok=True)

    name = safe_filename(upload_file.filename)
    if not name.lower().endswith(('.png', '.jpg', '.jpeg')):
        return {'error': 'File type not allowed. Use .png, .jpg, or .jpeg'}

    if not tooth_model_available():
        return {'error': 'Tooth segmentation model is not installed on this server.'}

    try:
        with open(upload_file.stream, 'rb') as f:
            pass  # stream already consumed; leave placeholder
    except Exception:
        pass

    # Read bytes safely (re-seek)
    stream = upload_file.stream
    if hasattr(stream, 'seek'):
        stream.seek(0)
    img_bytes = stream.read()

    base = uuid.uuid4().hex
    ext = os.path.splitext(name)[1] or '.jpg'
    stored_name = base + ext
    upload_path = os.path.join(_upload_dir(), stored_name)
    with open(upload_path, 'wb') as f:
        f.write(img_bytes)

    model = _get_model()
    input_img = preprocess(img_bytes)
    prediction = model.predict(input_img, verbose=0)
    mask = postprocess(prediction)

    mask_name = f'mask_{base}.png'
    mask_path = os.path.join(_upload_dir(), mask_name)
    Image.fromarray(mask).save(mask_path)

    coverage = int(np.mean(mask > 0) * 100)

    static_dir = 'ai_models'
    return {
        'orig_url': '%s/uploads/%s' % (static_dir, stored_name),
        'mask_url': '%s/uploads/%s' % (static_dir, mask_name),
        'coverage_percent': coverage,
    }
