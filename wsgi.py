"""WSGI entry point for production servers.

The dev runner is `run.py` (Flask's built-in server, debug on). This module is
the production-safe entry used by waitress, gunicorn, uwsgi, etc. and defaults
to the `production` configuration profile.

Examples:
    waitress-serve --host 0.0.0.0 --port 8080 wsgi:app
    gunicorn --bind 0.0.0.0:8080 wsgi:app
"""
import os

from app import create_app

app = create_app(os.environ.get('FLASK_CONFIG') or 'production')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))