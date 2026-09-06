"""Container / Space bootstrap for iHIS.

Runs before the web server starts (Docker entrypoint or the Hugging Face
Gradio wrapper):

1. Resolves configuration for an ephemeral demo host: if no ``DATABASE_URL``
   is provided and ``IHIS_EPHEMERAL_DEMO=1``, a SQLite file under ``var/``
   is used (data is lost when the container restarts — fine for a demo,
   never for a real hospital). ``SECRET_KEY`` is generated when missing.
2. Downloads the optional AI model weights from a Hugging Face *model* repo
   (``IHIS_MODELS_REPO``) — the weights are too large for git and the app
   degrades gracefully without them.
3. Applies migrations (``flask db upgrade``) and seeds roles (or the full
   synthetic demo when ``IHIS_DEMO_SEED=1``).

Idempotent: safe to run on every start.
"""
import os
import secrets
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)


def log(msg):
    print(f'[ihis-bootstrap] {msg}', flush=True)


def resolve_env():
    os.environ.setdefault('FLASK_CONFIG', 'production')
    if not os.environ.get('SECRET_KEY'):
        os.environ['SECRET_KEY'] = secrets.token_hex(32)
        log('SECRET_KEY was not set: generated a random one (sessions reset on restart). '
            'Set SECRET_KEY as a secret for a stable deployment.')
    if not os.environ.get('DATABASE_URL'):
        if os.environ.get('IHIS_EPHEMERAL_DEMO') == '1':
            os.makedirs(os.path.join(ROOT, 'var'), exist_ok=True)
            os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(ROOT, 'var', 'ihis-demo.db').replace('\\', '/')
            log('No DATABASE_URL: using an ephemeral SQLite demo database (IHIS_EPHEMERAL_DEMO=1).')
        else:
            log('ERROR: DATABASE_URL is required (PostgreSQL, e.g. Neon) unless IHIS_EPHEMERAL_DEMO=1.')
            sys.exit(2)
    os.environ.setdefault('IHIS_BEHIND_PROXY', '1')


def fetch_models():
    repo = os.environ.get('IHIS_MODELS_REPO')
    if not repo:
        log('IHIS_MODELS_REPO not set: image AI models stay unavailable (pages degrade gracefully).')
        return
    try:
        from huggingface_hub import snapshot_download
        target = os.path.join(ROOT, 'app', 'static', 'ai_models')
        os.makedirs(target, exist_ok=True)
        snapshot_download(repo_id=repo, repo_type='model', local_dir=target,
                          allow_patterns=['*.pt', '*.pth', '*.keras', '*.h5', '*.pkl'],
                          token=os.environ.get('HF_TOKEN'))
        present = [f for f in os.listdir(target) if f.endswith(('.pt', '.pth', '.keras', '.h5'))]
        log(f'Model weights ready: {present}')
    except Exception as exc:  # noqa: BLE001 - never block the app on model download
        log(f'Model download failed ({type(exc).__name__}: {exc}); continuing without image models.')


def migrate_and_seed():
    env = dict(os.environ, IHIS_SKIP_CREATE_ALL='1')
    log('Applying migrations (flask db upgrade)…')
    subprocess.run([sys.executable, '-m', 'flask', 'db', 'upgrade'], check=True, env=env)
    from app import create_app, db
    from app.models import User
    app = create_app(os.environ['FLASK_CONFIG'])
    with app.app_context():
        empty = User.query.count() == 0
    if os.environ.get('IHIS_DEMO_SEED') == '1':
        if empty:
            log('Seeding the synthetic demo hospital (IHIS_DEMO_SEED=1)…')
            subprocess.run([sys.executable, 'seed.py'], check=True, env=env)
        else:
            log('Demo seed skipped: database already has users.')
    else:
        log('Seeding roles and permissions only (seed.py --roles-only)…')
        subprocess.run([sys.executable, 'seed.py', '--roles-only'], check=True, env=env)


def main():
    resolve_env()
    fetch_models()
    migrate_and_seed()
    log('Bootstrap complete.')


if __name__ == '__main__':
    main()
