"""WSGI entry point for production servers.

The dev runner is `run.py` (Flask's built-in server, debug on). This module is
the production-safe entry used by waitress, gunicorn, uwsgi and PythonAnywhere.

It exposes BOTH names:
    * ``app``  - used by gunicorn/waitress (``wsgi:app``)
    * ``application`` - alias required by PythonAnywhere's WSGI loader.

It defaults to the ``production`` configuration profile and NEVER runs a
development/demo server: importing this module only builds the WSGI app, so it
is safe to import from a production WSGI file. ``if __name__ == '__main__'`` is
guarded and is only for a local manual smoke run.

Examples:
    waitress-serve --host 0.0.0.0 --port 8080 wsgi:app
    gunicorn --bind 0.0.0.0:8080 wsgi:app
    # PythonAnywhere WSGI file (/var/www/<user>_pythonanywhere_com_wsgi.py):
    #     import os, sys
    #     sys.path.insert(0, '/home/<user>/iHIS_Project')
    #     os.environ.setdefault('FLASK_CONFIG', 'production')
    #     from wsgi import application  # noqa
"""
import os

# Production convenience: if a `.env` file sits next to the project root,
# load it so SECRET_KEY / DATABASE_URL / etc. come from a non-committed file.
# Falls back silently when absent.
from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from app import create_app

app = create_app(os.environ.get('FLASK_CONFIG') or 'production')
application = app  # PythonAnywhere-compatible alias


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))