"""AI Tooth Segmentation service — U-Net dental segmentation.

Ports the standalone "Tooth Segmentation" project into iHIS. Two checkpoints are
supported; the module prefers the updated ``best_unet.keras`` when present and
otherwise falls back to the original ``dental_unet_model.keras``:

* ``best_unet.keras`` — 256x256 **RGB** input normalized by /255, single-channel
  binary mask output, threshold 0.5.
    Reference test performance: Accuracy = 96.16% | Dice = 84.76% |
    IoU = 73.56% | Loss = 0.1155
* ``dental_unet_model.keras`` — 256x256 **grayscale** input with CLAHE
  contrast enhancement, single-channel binary mask output.

The input pipeline (channels + normalization) is selected automatically from
the loaded model's declared input shape, so both checkpoints feed correctly.

The heavy TensorFlow model is lazy-loaded once (thread-safe). Falls back
gracefully when TensorFlow or no model file is available.
"""
import io
import os
import threading
import uuid
import numpy as np
import werkzeug

from flask import current_app

MODEL_NAMES = ('best_unet.keras', 'dental_unet_model.keras')
IMG_SIZE = (256, 256)

_lock = threading.Lock()
_loaded = False
_model = None
_channels = 1  # input channels of the loaded model (1=grayscale, 3=RGB)


def tooth_model_available():
    """True when at least one supported U-Net checkpoint is installed."""
    base = os.path.join(current_app.static_folder, 'ai_models')
    return any(os.path.exists(os.path.join(base, n)) for n in MODEL_NAMES)


def _model_path():
    """Return the preferred installed model path, or None if none present."""
    base = os.path.join(current_app.static_folder, 'ai_models')
    for name in MODEL_NAMES:
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return None


def _upload_dir():
    # Private directory for PAN/input and generated mask images. Never under
    # static/, so patient panoramic radiographs are not publicly served.
    base = current_app.config.get('UPLOAD_FOLDER') or 'var/uploads'
    return os.path.join(base, 'ai', 'tooth', 'uploads')


def _get_model():
    """Lazy-load the U-Net Keras model once (thread-safe)."""
    global _loaded, _model, _channels
    if _loaded:
        return _model
    with _lock:
        if _loaded:
            return _model
        path = _model_path()
        if not path:
            raise FileNotFoundError('U-Net model not found')
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

        def iou(y_true, y_pred, smooth=1e-6):
            """Intersection-over-Union metric referenced by the checkpoint."""
            y_true_f = K.flatten(y_true)
            y_pred_f = K.flatten(y_pred)
            intersection = K.sum(y_true_f * y_pred_f)
            union = K.sum(y_true_f) + K.sum(y_pred_f) - intersection
            return (intersection + smooth) / (union + smooth)

        try:
            _model = tf.keras.models.load_model(
                path,
                custom_objects={'dice_loss': dice_loss, 'dice_coef': dice_coef,
                                'bce_dice_loss': bce_dice_loss,
                                'iou': iou})
        except Exception as e:  # pragma: no cover - env-specific
            raise RuntimeError(f'Failed to load U-Net model: {e}')
        shape = getattr(_model, 'input_shape', None) or _model.inputs[0].shape
        _channels = int(shape[-1]) if shape and len(shape) >= 4 else 1
        _loaded = True
        return _model


def safe_filename(filename):
    try:
        return werkzeug.utils.secure_filename(filename)
    except Exception:
        return f'{uuid.uuid4().hex}.jpg'


def preprocess(image_bytes):
    """Preprocess the uploaded X-ray to match the loaded model's input.

    Uses the channel count detected from the loaded model:

    * 3 channels (``best_unet.keras``) -> RGB, no CLAHE, normalize /255 ->
      (1, 256, 256, 3).
    * 1 channel (``dental_unet_model.keras``) -> grayscale + CLAHE, normalize
      /255 -> (1, 256, 256, 1).
    """
    from PIL import Image
    if _channels >= 3:
        image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        image = image.resize(IMG_SIZE)
        arr = np.asarray(image, dtype=np.float32) / 255.0
        return np.expand_dims(arr, axis=0)

    import cv2
    image = Image.open(io.BytesIO(image_bytes)).convert('L')
    arr = np.array(image)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    arr = clahe.apply(arr)
    arr = cv2.resize(arr, IMG_SIZE) / 255.0
    arr = np.expand_dims(arr, axis=0)
    return np.expand_dims(arr, axis=-1)


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

    # These map to the protected AI media endpoint (see routes/ai.py
    # `ai_media`) which requires an authenticated session and the feature's
    # roles. Files are never placed under static/, so no public URL exists.
    return {
        'feature': 'tooth',
        'orig_key': stored_name,
        'mask_key': mask_name,
        'coverage_percent': coverage,
    }
