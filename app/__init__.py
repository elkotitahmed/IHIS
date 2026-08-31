import os
from flask import Flask, g, session, request
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect
from flask_cors import CORS
from config import config_map

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
bcrypt = Bcrypt()
csrf = CSRFProtect()
cors = CORS()


def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get('FLASK_CONFIG', 'development')

    app = Flask(__name__)
    app.config.from_object(config_map[config_name])

    if config_name == 'production':
        weak = {'ihis-dev-secret-key-change-me', 'change-me-to-a-long-random-string'}
        if not app.config.get('SECRET_KEY') or app.config['SECRET_KEY'] in weak:
            raise RuntimeError(
                'Production requires a strong SECRET_KEY. '
                'Set the SECRET_KEY environment variable before starting the app.')

    # Ensure upload folder exists
    os.makedirs(app.config.get('UPLOAD_FOLDER', 'app/static/uploads'), exist_ok=True)
    os.makedirs(os.path.join(os.path.dirname(__file__), '..', 'database'), exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)

    # CORS is restricted to configured origins only. The app is a same-origin
    # server-rendered Flask application; if no CORS_ORIGINS is configured we do
    # NOT open cross-origin access (defaults to deny), preventing cross-site
    # state-changing requests against the session-cookie-authenticated API.
    allowed_origins = app.config.get('CORS_ORIGINS')
    if allowed_origins:
        if isinstance(allowed_origins, str):
            allowed_origins = [o.strip() for o in allowed_origins.split(',') if o.strip()]
        cors.init_app(app, resources={r'/*': {'origins': allowed_origins}})

    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'warning'
    login_manager.login_message = 'Please log in to access this page.'

    # Register blueprints
    from app.routes.main import main_bp
    from app.routes.auth import auth_bp
    from app.routes.patient import patient_bp
    from app.routes.doctor import doctor_bp
    from app.routes.lab import lab_bp
    from app.routes.radiology import radiology_bp
    from app.routes.pharmacy import pharmacy_bp
    from app.routes.nursing import nursing_bp
    from app.routes.reception import reception_bp
    from app.routes.dentistry import dentistry_bp
    from app.routes.physiotherapy import physiotherapy_bp
    from app.routes.admin import admin_bp
    from app.routes.super_admin import super_admin_bp
    from app.routes.api import api_bp
    from app.routes.reports import reports_bp
    from app.routes.ai import ai_bp
    from app.routes.care import care_bp
    from app.routes.billing import billing_bp
    from app.routes.admissions import admissions_bp
    from app.routes.tasks import tasks_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(patient_bp, url_prefix='/patient')
    app.register_blueprint(doctor_bp, url_prefix='/doctor')
    app.register_blueprint(lab_bp, url_prefix='/lab')
    app.register_blueprint(radiology_bp, url_prefix='/radiology')
    app.register_blueprint(pharmacy_bp, url_prefix='/pharmacy')
    app.register_blueprint(nursing_bp, url_prefix='/nursing')
    app.register_blueprint(reception_bp, url_prefix='/reception')
    app.register_blueprint(dentistry_bp, url_prefix='/dentistry')
    app.register_blueprint(physiotherapy_bp, url_prefix='/physiotherapy')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(super_admin_bp, url_prefix='/super-admin')
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(reports_bp, url_prefix='/reports')
    app.register_blueprint(ai_bp, url_prefix='/ai')
    app.register_blueprint(care_bp, url_prefix='/care')
    app.register_blueprint(billing_bp, url_prefix='/billing')
    app.register_blueprint(admissions_bp, url_prefix='/admissions')
    app.register_blueprint(tasks_bp, url_prefix='/tasks')

    # In production the schema is owned by Alembic migrations (`flask db
    # upgrade`). For local development the convenience of auto-creating missing
    # tables from the models is harmless, but it must never shadow the migration
    # process in production.
    if config_name != 'production':
        with app.app_context():
            db.create_all()

    register_context_processors(app)

    return app


def register_context_processors(app):
    """Provide role-based sidebar menus to all templates."""

    def menus():
        from flask_login import current_user
        if not current_user.is_authenticated:
            return []
        items = []
        if current_user.has_any_role('Patient') or current_user.user_type == 'patient':
            items += [
                {'label': 'Medical History', 'url': '/patient/medical-history', 'icon': 'fa-history'},
                {'label': 'Appointments', 'url': '/patient/appointments', 'icon': 'fa-calendar-check'},
                {'label': 'Prescriptions', 'url': '/patient/prescriptions', 'icon': 'fa-pills'},
                {'label': 'Lab Results', 'url': '/patient/lab-results', 'icon': 'fa-flask'},
                {'label': 'Radiology Reports', 'url': '/patient/radiology-reports', 'icon': 'fa-x-ray'},
                {'label': 'Documents', 'url': '/patient/documents', 'icon': 'fa-folder-open'},
                {'label': 'Bills', 'url': '/patient/bills', 'icon': 'fa-file-invoice-dollar'},
                {'label': 'Messages', 'url': '/patient/messages', 'icon': 'fa-envelope'},
            ]
        if current_user.has_any_role('Doctor'):
            items += [
                {'label': 'Patients', 'url': '/doctor/patients', 'icon': 'fa-user-md'},
                {'label': 'Appointments', 'url': '/doctor/appointments', 'icon': 'fa-calendar-check'},
                {'label': 'Lab Results', 'url': '/doctor/lab-results', 'icon': 'fa-flask'},
            ]
        if current_user.has_any_role('LabTechnician'):
            items += [
                {'label': 'Lab Orders', 'url': '/lab/orders', 'icon': 'fa-flask'},
                {'label': 'Test Catalog', 'url': '/lab/catalog', 'icon': 'fa-book'},
            ]
        if current_user.has_any_role('Radiologist'):
            items += [{'label': 'Radiology Orders', 'url': '/radiology/orders', 'icon': 'fa-x-ray'}]
        if current_user.has_any_role('Pharmacist'):
            items += [{'label': 'Dashboard', 'url': '/pharmacy/dashboard', 'icon': 'fa-pills'}]
        if current_user.has_any_role('Nurse'):
            items += [{'label': 'Dashboard', 'url': '/nursing/dashboard', 'icon': 'fa-stethoscope'}]
        if current_user.has_any_role('Receptionist'):
            items += [
                {'label': 'Dashboard', 'url': '/reception/dashboard', 'icon': 'fa-concierge-bell'},
                {'label': 'Admissions', 'url': '/admissions/dashboard', 'icon': 'fa-door-open'},
                {'label': 'Billing', 'url': '/billing/dashboard', 'icon': 'fa-file-invoice-dollar'},
            ]
        if current_user.has_any_role('Admin', 'SuperAdmin'):
            items += [
                {'label': 'Billing', 'url': '/billing/dashboard', 'icon': 'fa-file-invoice-dollar'},
                {'label': 'Admissions', 'url': '/admissions/dashboard', 'icon': 'fa-door-open'},
            ]
        if current_user.has_any_role('Dentist'):
            items += [{'label': 'Dashboard', 'url': '/dentistry/dashboard', 'icon': 'fa-tooth'}]
        if current_user.has_any_role('Physiotherapist'):
            items += [{'label': 'Dashboard', 'url': '/physiotherapy/dashboard', 'icon': 'fa-person-walking'}]
        # Any authenticated staff user gets access to the shared task queue.
        if current_user.has_any_role('SuperAdmin', 'Admin', 'Doctor', 'Nurse',
                                     'LabTechnician', 'Radiologist', 'Pharmacist',
                                     'Receptionist', 'Dentist', 'Physiotherapist'):
            items += [{'label': 'My Tasks', 'url': '/tasks/my-tasks', 'icon': 'fa-tasks'}]
        # Admin / SuperAdmin additions are handled by the dashboard main card,
        # so keep the sidebar portal-focused and merge by endpoint (dedupe).
        seen = set()
        merged = []
        for item in items:
            if item['url'] in seen:
                continue
            seen.add(item['url'])
            merged.append(item)
        return merged

    app.context_processor(lambda: {'current_user_menus': menus})

    def unread_count():
        from flask_login import current_user
        if not current_user.is_authenticated:
            return 0
        from app.models import Notification
        return Notification.query.filter_by(
            user_id=current_user.id, is_read=False).count()

    app.context_processor(lambda: {'unread_notifications': unread_count()})

    @app.before_request
    def set_language():
        lang = request.args.get('lang')
        if lang in ('en', 'ar'):
            session['lang'] = lang
        g.lang = session.get('lang', 'en')

    app.context_processor(lambda: {'g': g})
