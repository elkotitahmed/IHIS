"""Structured logging and request correlation for iHIS.

Provides:
  - `setup_logging(app)`: configure a JSON-friendly request logger with a
    per-request correlation/request ID injected via `g`.
  - `get_request_id()`: retrieve/create the current request's correlation ID.
  - before/after request hooks that emit an access log line per request with
    latency.

The request ID is threaded through `g.request_id` and is also available to the
AuditLog writer so backend logs can be correlated with the DB audit trail.
"""
import logging
import time
import uuid
from flask import g, request

logger = logging.getLogger('ihis.request')


def get_request_id():
    """Return the current request's correlation ID, generating one if missing."""
    if not hasattr(g, 'request_id'):
        incoming = request.headers.get('X-Request-ID')
        g.request_id = (incoming or uuid.uuid4().hex)[:64]
    return g.request_id


def setup_logging(app):
    """Attach request/response logging with correlation IDs to the app."""
    formatter = logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s'
    )
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    app.logger.addHandler(stream)
    app.logger.setLevel(logging.DEBUG if app.debug else logging.INFO)

    @app.before_request
    def _attach_request_id_and_start():
        g.request_id = get_request_id()
        g._start = time.perf_counter()

    @app.after_request
    def _log_request(response):
        # Static assets are normally served by the reverse proxy (nginx);
        # skip them here to reduce log noise.
        if request.path.startswith('/static/'):
            return response
        elapsed_ms = (time.perf_counter() - g.get('_start', time.perf_counter())) * 1000
        app.logger.info(
            '"%s %s" %d %.2fms',
            request.method, request.path, response.status_code, elapsed_ms,
            extra={'request_id': getattr(g, 'request_id', '-')},
        )
        return response

    return app
