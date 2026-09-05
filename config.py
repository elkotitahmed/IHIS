import os
from datetime import timedelta
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))


class Config:
    """Base configuration for the iHIS application."""

    SECRET_KEY = os.environ.get('SECRET_KEY') or 'ihis-dev-secret-key-change-me'

    # Database (SQLite by default; swap DATABASE_URL for PostgreSQL/MySQL)
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'database', 'ihis.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Session
    REMEMBER_COOKIE_DURATION = timedelta(days=7)
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)

    # File uploads
    UPLOAD_FOLDER = os.path.join(basedir, 'var', 'uploads')  # private, NOT under static/
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024  # 32 MB
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'dcm', 'mp4', 'webm'}
    RADIOLOGY_AI_PACKAGE_DIR = os.environ.get(
        'RADIOLOGY_AI_PACKAGE_DIR',
        os.path.join(basedir, 'AI apps', 'RadiologyAI_Integration_Package',
                     'RadiologyAI_Integration_Package'))

    # Pagination
    ITEMS_PER_PAGE = 20

    # CORS: comma-separated allowed origins. Empty by default for the
    # same-origin server-rendered app. Set explicitly (e.g. to a trusted SPA
    # origin) only if a separate frontend needs to call the JSON API cross-origin.
    CORS_ORIGINS = os.environ.get('CORS_ORIGINS', '')

    # Security: login lockout
    MAX_LOGIN_ATTEMPTS = 5
    LOCKOUT_MINUTES = 15

    # Password policy
    PASSWORD_MIN_LENGTH = 8

    # Rate limiting (Phase 15). Enabled in production; disabled in tests to
    # avoid interfering with functional test assertions. Storage uses the
    # in-memory backend unless RATELIMIT_STORAGE_URI is provided (e.g. Redis).
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = True

    # Production must provide SECRET_KEY and DATABASE_URL from the environment.
    # Weak or absent values raise an error at boot (see create_app).
    SECRET_KEY = os.environ.get('SECRET_KEY') or Config.SECRET_KEY

    # PostgreSQL is the expected production database.
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'database', 'ihis.db')

    # Password policy: production enforces minimum length >= 12
    PASSWORD_MIN_LENGTH = 12


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    # bcrypt work factor is deliberately low in the test profile so the suite
    # is not dominated by password hashing; production keeps the default (12).
    BCRYPT_LOG_ROUNDS = 4
    # Rate limiting is normally off for functional tests to keep them hermetic
    # and avoid cross-test interference on shared counter storage. Set the
    # RATELIMIT_ENABLED=1 env var to exercise the limit in a dedicated test.
    RATELIMIT_ENABLED = os.environ.get('RATELIMIT_ENABLED', '0') == '1'


config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
}