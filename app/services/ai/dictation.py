"""Local speech-to-text for clinical dictation (faster-whisper, MIT licence).

Model ``base`` quantised to int8 (~75 MB, downloaded once from Hugging Face on
first use), runs on CPU. Audio never leaves the server; nothing is stored.
The transcript is a draft: the clinician edits it before it touches the chart.
"""
import os
import tempfile
import threading

MODEL_SIZE = os.environ.get('IHIS_WHISPER_MODEL', 'base')
_lock = threading.Lock()
_model = None


def dictation_available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from faster_whisper import WhisperModel
                _model = WhisperModel(MODEL_SIZE, device='cpu', compute_type='int8')
    return _model


def transcribe(file_storage, language=None):
    """Return {'text', 'language', 'duration'} or {'error'}."""
    if not dictation_available():
        return {'error': 'Dictation model is not installed on this server.'}
    suffix = os.path.splitext(file_storage.filename or 'audio.webm')[1] or '.webm'
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        file_storage.save(path)
        model = _get_model()
        segments, info = model.transcribe(path, beam_size=1, language=language or None, vad_filter=True)
        text = ' '.join(s.text.strip() for s in segments).strip()
        return {'text': text, 'language': info.language, 'duration': round(float(info.duration or 0), 1)}
    except Exception as e:  # noqa: BLE001
        return {'error': f'Transcription failed: {e}'}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
