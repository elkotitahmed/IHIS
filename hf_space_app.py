"""Hugging Face Space entry (Gradio SDK) that hosts the full iHIS Flask app.

Why this file exists: on a free personal Hugging Face account only Static
and ZeroGPU (Gradio-SDK) Spaces can be created. This wrapper serves the whole
Flask application under ``/ihis`` from the same port the Space exposes, with
a tiny Gradio landing page at ``/``. The mandatory ``@spaces.GPU`` function
is a no-op that is never called by the app, so no GPU quota is used.

Locally:  python hf_space_app.py   →  http://127.0.0.1:7860/ihis/
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

try:                       # ZeroGPU runtime only; harmless elsewhere
    import spaces          # noqa: F401  (must be imported before torch)
    _HAS_SPACES = True
except Exception:          # noqa: BLE001
    _HAS_SPACES = False

os.environ.setdefault('IHIS_EPHEMERAL_DEMO', '1')
os.environ.setdefault('IHIS_DEMO_SEED', '1')
os.environ.setdefault('FLASK_CONFIG', 'production')

from deployment import bootstrap  # noqa: E402

bootstrap.main()

import gradio as gr  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from a2wsgi import WSGIMiddleware  # noqa: E402

from wsgi import app as flask_app  # noqa: E402

APP_PREFIX = '/ihis'

if _HAS_SPACES:
    @spaces.GPU(duration=5)
    def _gpu_placeholder():
        """Required by ZeroGPU Spaces; never invoked by the hospital app."""
        return 'ok'

api = FastAPI(title='iHIS')
api.mount(APP_PREFIX, WSGIMiddleware(flask_app))


@api.get('/health')
def health():
    return {'status': 'ok', 'app': APP_PREFIX}


with gr.Blocks(title='iHIS — Hospital AI Copilot') as demo:
    gr.Markdown(
        "# 🏥 iHIS — Integrated Hospital Information System\n"
        "An AI Clinical Copilot for physicians and a safe health assistant for patients.\n\n"
        f"### 👉 [Open the hospital system]({APP_PREFIX}/)\n\n"
        "Demo accounts (synthetic data only, password `123456`): `superadmin@ihis.com`, "
        "`dr.ahmed@ihis.com`, `radio@ihis.com`, `pharma@ihis.com`, `nurse@ihis.com`, "
        "`dentist@ihis.com`, `patient@ihis.com`.\n\n"
        "*Deterministic clinical rules are authoritative; AI is optional, labelled and reviewed by a clinician. "
        "This demo runs on an ephemeral database that resets on restart.*"
    )
    if _HAS_SPACES:
        gr.Button('Open iHIS', link=f'{APP_PREFIX}/')

app = gr.mount_gradio_app(api, demo, path='/')


@api.get('/app')
def go_app():
    return RedirectResponse(url=f'{APP_PREFIX}/')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 7860)))
