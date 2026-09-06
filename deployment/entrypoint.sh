#!/bin/sh
# iHIS container entrypoint: bootstrap (env, models, migrations, seed) then serve.
set -e
cd "$(dirname "$0")/.."
python deployment/bootstrap.py
# Re-export values the bootstrap may have generated (SECRET_KEY / DATABASE_URL)
# by running the server through the same Python process environment.
exec python -c "
import os, subprocess, sys
sys.path.insert(0, os.getcwd())
from deployment.bootstrap import resolve_env
resolve_env()
port = os.environ.get('PORT', '7860')
workers = os.environ.get('WEB_CONCURRENCY', '2')
os.execvp('gunicorn', ['gunicorn', '--bind', f'0.0.0.0:{port}', '--workers', workers,
                       '--threads', '4', '--timeout', '180', '--access-logfile', '-', 'wsgi:app'])
"
