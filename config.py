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
    UPLOAD_FOLDER = os.path.join(basedir, 'app', 'static', 'uploads')
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024  # 32 MB
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'dcm', 'mp4', 'webm'}

    # Pagination
    ITEMS_PER_PAGE = 20

    # Security: login lockout
    MAX_LOGIN_ATTEMPTS = 5
    LOCKOUT_MINUTES = 15


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    # Production must NOT use the development fallback secret (see create_app
    # validation): SECRET_KEY is expected from the environment. Session cookies
    # are locked down since production runs over HTTPS.
    SECRET_KEY = os.environ.get('SECRET_KEY') or Config.SECRET_KEY
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = True


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
}