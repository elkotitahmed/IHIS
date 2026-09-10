import os
from flask import Flask, g, session, request, abort
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from config import config_map
from app.i18n import register_i18n

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
bcrypt = Bcrypt()
csrf = CSRFProtect()
cors = CORS()
limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri="memory://")


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
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if not db_uri or db_uri.startswith('sqlite:'):
            if os.environ.get('IHIS_EPHEMERAL_DEMO') == '1' and db_uri.startswith('sqlite:'):
                # Ephemeral demo hosts (e.g. a Hugging Face Space without a
                # database) may run production settings on a throw-away SQLite
                # file. Data does not survive a restart; never use for real care.
                app.logger.warning('IHIS_EPHEMERAL_DEMO=1: production profile on an ephemeral '
                                   'SQLite database. Demo use only.')
            else:
                raise RuntimeError(
                    'Production requires an explicit server DATABASE_URL '
                    '(e.g. postgresql+psycopg2://...). Running production on '
                    'SQLite is not supported.')

    if os.environ.get('IHIS_BEHIND_PROXY') == '1':
        # Behind a TLS-terminating proxy (Hugging Face, Cloud Run, nginx):
        # trust X-Forwarded-Proto/Host so url_for builds https links.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # Ensure upload folder exists
    os.makedirs(app.config.get('UPLOAD_FOLDER', 'app/static/uploads'), exist_ok=True)
    os.makedirs(os.path.join(os.path.dirname(__file__), '..', 'database'), exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)

    # Rate limiting (Phase 15). Flask-Limiter reads RATELIMIT_ENABLED and
    # RATELIMIT_STORAGE_URI from config at init_app; storage is configured even
    # when disabled so a dedicated test can enable it later.
    limiter.init_app(app)

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
    from app.routes.clinical import clinical_bp
    from app.routes.search import search_bp
    from app.routes.fhir import fhir_bp

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
    app.register_blueprint(clinical_bp, url_prefix='/clinical')
    app.register_blueprint(search_bp, url_prefix='/search')
    app.register_blueprint(fhir_bp, url_prefix='/fhir')
    from app.routes.copilot import copilot_bp
    app.register_blueprint(copilot_bp)
    from app.routes.patient_ai import patient_ai_bp
    app.register_blueprint(patient_ai_bp)

    # In production the schema is owned by Alembic migrations (`flask db
    # upgrade`). For local development the convenience of auto-creating missing
    # tables from the models is harmless, but it must never shadow the migration
    # process in production.
    # Skipped under the `flask db ...` CLI (and when IHIS_SKIP_CREATE_ALL is
    # set) so Alembic, not create_all, builds the schema when migrating.
    import sys as _sys
    _migrating = (_sys.argv[1:2] == ['db'] or 'flask' in _sys.argv[0:1] and 'db' in _sys.argv[1:3]
                  or os.environ.get('IHIS_SKIP_CREATE_ALL') == '1')
    if config_name != 'production' and not _migrating:
        with app.app_context():
            db.create_all()

    register_i18n(app)
    register_context_processors(app)
    register_error_handlers(app)

    from app.services.logging import setup_logging
    setup_logging(app)

    # Block public serving of private medical/AI files that might still reside
    # under the static tree (legacy `static/uploads/...` records, AI uploads,
    # results and model inputs). Even though new files are written to the
    # private UPLOAD_FOLDER, this is a defensive guard so a misconfigured or
    # legacy layout can never expose PHI at a public `/static/...` URL.
    @app.before_request
    def block_private_static():
        if request.path.startswith('/static/') and (
            '/static/uploads/' in request.path or '/static/ai_models/' in request.path
        ):
            abort(404)
        return None

    # Production-safe security headers (Phase 16). Applied to every response.
    # HSTS is only meaningful over HTTPS and is set in production to avoid
    # sending it during local http dev.
    @app.after_request
    def set_security_headers(response):
        is_prod = config_name == 'production'
        if is_prod:
            response.headers.setdefault(
                'Strict-Transport-Security',
                'max-age=31536000; includeSubDomains')
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        return response

    return app


def register_context_processors(app):
    """Provide role-based sidebar menus, unread counts, and language to all templates."""

    @app.template_filter('physician_label')
    def physician_label(value):
        """Display terminology: the internal role/identifier 'Doctor' is shown
        as 'Physician' (internal names, DB values and API contracts unchanged)."""
        if value is None:
            return ''
        text = str(value)
        return (text.replace('Doctors', 'Physicians').replace('doctors', 'physicians')
                    .replace('Doctor', 'Physician').replace('doctor', 'physician'))

    @app.template_filter('mrn_label')
    def mrn_label(value):
        """Render a medical record number with a single 'MRN' prefix.

        Seeded MRNs look like ``P10001`` while assigned ones look like
        ``MRN-000042``; templates used to print ``MRN MRN-000042``."""
        if not value:
            return ''
        text = str(value)
        return text if text.upper().startswith('MRN') else f'MRN {text}'

    ROLE_LABELS = {
        'SuperAdmin': 'Super Administrator',
        'Admin': 'Administrator',
        'Doctor': 'Physician',
        'Nurse': 'Nurse',
        'LabTechnician': 'Lab Technician',
        'Radiologist': 'Radiologist',
        'RadiologyTechnician': 'Radiology Technician',
        'Pharmacist': 'Pharmacist',
        'Receptionist': 'Receptionist',
        'Dentist': 'Dentist',
        'Physiotherapist': 'Physiotherapist',
        'Cashier': 'Cashier',
        'Patient': 'Patient',
    }

    def _get_effective_roles(user):
        """Return the roles to use for navigation. If SuperAdmin is previewing,
        return the preview role(s) instead of the real roles."""
        preview = session.get('preview_role')
        if preview and user.has_role('SuperAdmin'):
            return [preview]
        return [r.name for r in user.roles]

    def menus():
        from flask_login import current_user
        if not current_user.is_authenticated:
            return []
        lang = getattr(g, 'lang', session.get('lang', 'en'))
        items = []

        def _l(en, ar):
            return ar if lang == 'ar' else en

        def _ai_tools(role_set):
            """Zero-argument AI tools the given roles can reach, in display order."""
            specs = [
                (_l('Chest X-ray Screening', 'فحص أشعة الصدر'), '/ai/chest-xray',
                 'fa-lungs', {'Radiologist', 'RadiologyTechnician', 'Doctor', 'Admin', 'SuperAdmin'}),
                (_l('Head CT Haemorrhage', 'نزف الدماغ (CT)'), '/ai/ich-detection',
                 'fa-brain', {'Radiologist', 'RadiologyTechnician', 'Doctor', 'Admin', 'SuperAdmin'}),
                (_l('Fracture Detection', 'كشف الكسور'), '/ai/fracture-detection',
                 'fa-bone', {'Radiologist', 'Doctor', 'Nurse', 'Physiotherapist',
                             'Dentist', 'Admin', 'SuperAdmin'}),   # = route decorator
                (_l('Tooth Segmentation', 'تجزئة الأسنان'), '/ai/tooth-segmentation',
                 'fa-tooth', {'Dentist', 'Radiologist', 'Nurse', 'Admin', 'SuperAdmin'}),   # = route decorator
                (_l('Skin Lesion Detection', 'كشف آفات الجلد'), '/ai/skin-lesion-detection',
                 'fa-person-circle-question', {'Doctor', 'Dentist', 'Nurse',
                                               'Admin', 'SuperAdmin'}),
                (_l('Health Insights', 'الرؤى الصحية'), '/ai/health-insights',
                 'fa-brain', {'Patient', 'Admin', 'SuperAdmin'}),
                (_l('Clinical Pharmacist AI', 'الصيدلاني السريري'), '/pharmacy/ai-workbench',
                 'fa-user-doctor', {'Pharmacist', 'Admin', 'SuperAdmin'}),
            ]
            from app.services.ai.specialty_models import allowed_models, KEY_BY_ENDPOINT
            allowed = allowed_models(current_user, role_set)
            return [{'label': label, 'url': url, 'icon': icon}
                    for label, url, icon, roles in specs
                    if role_set & roles and (url not in KEY_BY_ENDPOINT or KEY_BY_ENDPOINT[url] in allowed)]

        effective_roles = _get_effective_roles(current_user)
        role_set = set(effective_roles)
        is_superadmin_real = current_user.has_role('SuperAdmin')
        is_previewing = bool(session.get('preview_role'))

        def _has_menu_permission(*permission_names):
            """Use the effective role set when deciding which links to show."""
            if is_superadmin_real and not is_previewing:
                return True
            effective_role_names = role_set or {role.name for role in current_user.roles}
            return any(
                permission.name in permission_names
                for role in current_user.roles
                if role.name in effective_role_names
                for permission in role.permissions
            )

        # SuperAdmin always gets Command Center + full navigation
        if is_superadmin_real and not is_previewing:
            items += [
                {'section': _l('COMMAND CENTER', 'مركز الأوامر'), 'items': [
                    {'label': _l('Hospital Overview', 'نظرة عامة على المستشفى'), 'url': '/super-admin/dashboard', 'icon': 'fa-gauge-high'},
                    {'label': _l('System Health', 'صحة النظام'), 'url': '/super-admin/system-health', 'icon': 'fa-heart-pulse'},
                    {'label': _l('AI Control Center', 'مركز التحكم بالذكاء'), 'url': '/super-admin/ai-control', 'icon': 'fa-wand-magic-sparkles'},
                    {'label': _l('Platform Capabilities', 'قدرات المنصة'), 'url': '/super-admin/capabilities', 'icon': 'fa-rocket'},
                ]},
                {'section': _l('CLINICAL', 'سريري'), 'items': [
                    {'label': _l('Patients', 'المرضى'), 'url': '/doctor/patients', 'icon': 'fa-user-injured'},
                    {'label': _l('Clinical Workbench', 'منصة سريرية'), 'url': '/clinical', 'icon': 'fa-stethoscope'},
                    {'label': _l('Clinical Alerts', 'التنبيهات السريرية'), 'url': '/clinical/alerts', 'icon': 'fa-bell'},
                    {'label': _l('Clinical Inbox', 'الصندوق السريري'), 'url': '/clinical/inbox', 'icon': 'fa-inbox'},
                    {'label': _l('Reminders', 'التذكيرات'), 'url': '/clinical/reminders', 'icon': 'fa-bell'},
                    {'label': _l('Recall Board', 'لوحة الاستدعاء'), 'url': '/clinical/recall-board', 'icon': 'fa-calendar-check'},
                    {'label': _l('Order Sets', 'حزم الأوامر'), 'url': '/clinical/order-sets', 'icon': 'fa-layer-group'},
                    {'label': _l('Clinical Templates', 'القوالب السريرية'), 'url': '/clinical/templates', 'icon': 'fa-clipboard-list'},
                ]},
                {'section': _l('OPERATIONS', 'العمليات'), 'items': [
                    {'label': _l('Reception', 'الاستقبال'), 'url': '/reception/dashboard', 'icon': 'fa-concierge-bell'},
                    {'label': _l('Appointments', 'المواعيد'), 'url': '/reception/appointments', 'icon': 'fa-calendar-check'},
                    {'label': _l('Task Queue', 'قائمة المهام'), 'url': '/tasks/queue', 'icon': 'fa-clipboard-list'},
                    {'label': _l('Admissions', 'الاستشفاء'), 'url': '/admissions/dashboard', 'icon': 'fa-door-open'},
                    {'label': _l('Referrals', 'الإحالات'), 'url': '/care/referrals', 'icon': 'fa-share-nodes'},
                    {'label': _l('Multidisciplinary Cases', 'الحالات متعددة التخصصات'), 'url': '/care/cases', 'icon': 'fa-people-group'},
                ]},
                {'section': _l('DIAGNOSTICS', 'التشخيص'), 'items': [
                    {'label': _l('Laboratory', 'المختبر'), 'url': '/lab/orders', 'icon': 'fa-flask'},
                    {'label': _l('Radiology', 'الأشعة'), 'url': '/radiology/orders', 'icon': 'fa-x-ray'},
                ]},
                {'section': _l('MEDICATION', 'الأدوية'), 'items': [
                    {'label': _l('Pharmacy', 'الصيدلية'), 'url': '/pharmacy/dashboard', 'icon': 'fa-pills'},
                    {'label': _l('Inventory', 'المخزون'), 'url': '/pharmacy/inventory', 'icon': 'fa-boxes-stacked'},
                ]},
                {'section': _l('SPECIALTIES', 'التخصصات'), 'items': [
                    {'label': _l('Nursing', 'التمريض'), 'url': '/nursing/dashboard', 'icon': 'fa-user-nurse'},
                    {'label': _l('Physiotherapy', 'العلاج الطبيعي'), 'url': '/physiotherapy/patients', 'icon': 'fa-person-walking'},
                    {'label': _l('Dentistry', 'الأسنان'), 'url': '/dentistry/patients', 'icon': 'fa-tooth'},
                ]},
                {'section': _l('FINANCE', 'المالية'), 'items': [
                    {'label': _l('Billing', 'الفواتير'), 'url': '/billing/dashboard', 'icon': 'fa-file-invoice-dollar'},
                    {'label': _l('Reports', 'التقارير'), 'url': '/reports/', 'icon': 'fa-chart-pie'},
                ]},
                {'section': _l('ADMINISTRATION', 'الإدارة'), 'items': [
                    {'label': _l('Users', 'المستخدمون'), 'url': '/admin/staff', 'icon': 'fa-users-cog'},
                    {'label': _l('Departments', 'الأقسام'), 'url': '/admin/departments', 'icon': 'fa-building'},
                    {'label': _l('Capacity & No-shows', 'السعة والغياب'), 'url': '/admin/capacity', 'icon': 'fa-gauge-high'},
                    {'label': _l('Roles & Permissions', 'الأدوار والصلاحيات'), 'url': '/super-admin/roles', 'icon': 'fa-shield-halved'},
                    {'label': _l('Audit Logs', 'سجلات المراجعة'), 'url': '/super-admin/audit-logs', 'icon': 'fa-clock-rotate-left'},
                    {'label': _l('Backup', 'النسخ الاحتياطي'), 'url': '/super-admin/backup', 'icon': 'fa-database'},
                    {'label': _l('Settings', 'الإعدادات'), 'url': '/super-admin/settings', 'icon': 'fa-gear'},
                ]},
            ]
        else:
            # Role-based navigation (for regular users OR preview mode)
            role_set = set(effective_roles)

            if 'Patient' in role_set or current_user.user_type == 'patient':
                items += [
                    {'section': _l('MY HEALTH', 'صحتي'), 'items': [
                        {'label': _l('My Health Summary', 'ملخص صحتي'), 'url': '/patient/health-summary', 'icon': 'fa-heart-pulse'},
                        {'label': _l('My Profile', 'ملفي'), 'url': '/patient/profile', 'icon': 'fa-id-card'},
                        {'label': _l('Medical History', 'التاريخ الطبي'), 'url': '/patient/medical-history', 'icon': 'fa-history'},
                    ]},
                    {'section': _l('MY APPOINTMENTS', 'مواعيدي'), 'items': [
                        {'label': _l('Appointments', 'المواعيد'), 'url': '/patient/appointments', 'icon': 'fa-calendar-check'},
                        {'label': _l('Book Appointment', 'حجز موعد'), 'url': '/patient/appointments/book', 'icon': 'fa-calendar-plus'},
                        {'label': _l('My Follow-up', 'متابعتي'), 'url': '/patient/health-summary#followup', 'icon': 'fa-calendar-day'},
                    ]},
                    {'section': _l('MY MEDICATIONS & RESULTS', 'أدويتي ونتائجي'), 'items': [
                        {'label': _l('My Medications', 'أدويتي'), 'url': '/patient/prescriptions', 'icon': 'fa-pills'},
                        {'label': _l('My Results', 'نتائجي'), 'url': '/patient/lab-results', 'icon': 'fa-flask'},
                        {'label': _l('My Radiology', 'أشعتي'), 'url': '/patient/my-radiology', 'icon': 'fa-x-ray'},
                    ]},
                    {'section': _l('MY DOCUMENTS & MESSAGES', 'مستنداتي ورسائلي'), 'items': [
                        {'label': _l('My Documents', 'مستنداتي'), 'url': '/patient/documents', 'icon': 'fa-folder-open'},
                        {'label': _l('My Messages', 'رسائلي'), 'url': '/patient/messages', 'icon': 'fa-envelope'},
                        {'label': _l('My Notifications', 'إشعاراتي'), 'url': '/notifications', 'icon': 'fa-bell'},
                        {'label': _l('Bills', 'الفواتير'), 'url': '/patient/bills', 'icon': 'fa-file-invoice-dollar'},
                    ]},
                ]

            if 'Doctor' in role_set:
                # PRACTICE = the daily essentials; everything else lives under WORK
                # (collapsed by default) so the sidebar stays calm.
                practice = [
                    {'label': _l('Patients', 'المرضى'), 'url': '/doctor/patients', 'icon': 'fa-user-injured'},
                    {'label': _l('Appointments', 'المواعيد'), 'url': '/doctor/appointments', 'icon': 'fa-calendar-check'},
                ]
                if _has_menu_permission('INBOX_VIEW'):
                    practice.append({'label': _l('Clinical Inbox', 'الصندوق السريري'), 'url': '/clinical/inbox', 'icon': 'fa-inbox'})
                if _has_menu_permission('ALERT_VIEW'):
                    practice.append({'label': _l('Clinical Alerts', 'التنبيهات السريرية'), 'url': '/clinical/alerts', 'icon': 'fa-bell'})
                items += [
                    {'section': _l('PRACTICE', 'الممارسة'), 'items': practice},
                    {'section': _l('WORK', 'العمل'), 'items': [
                        {'label': _l('Lab Results', 'نتائج المختبر'), 'url': '/doctor/lab-results', 'icon': 'fa-flask'},
                        {'label': _l('Admissions', 'الاستشفاء'), 'url': '/admissions/dashboard', 'icon': 'fa-door-open'},
                        {'label': _l('Referrals', 'الإحالات'), 'url': '/care/referrals', 'icon': 'fa-share-nodes'},
                        {'label': _l('Attachments', 'المرفقات'), 'url': '/doctor/attachments', 'icon': 'fa-paperclip'},
                    ]},
                ]

            if 'LabTechnician' in role_set:
                items += [
                    {'section': _l('LABORATORY', 'المختبر'), 'items': [
                        {'label': _l('Lab Dashboard', 'لوحة المختبر'), 'url': '/lab/dashboard', 'icon': 'fa-flask'},
                        {'label': _l('Work Queue', 'قائمة العمل'), 'url': '/lab/orders', 'icon': 'fa-layer-group'},
                        {'label': _l('Test Catalog', 'دليل الفحوصات'), 'url': '/lab/catalog', 'icon': 'fa-book'},
                    ]},
                ]

            if 'Radiologist' in role_set:
                items += [
                    {'section': _l('RADIOLOGY', 'الأشعة'), 'items': [
                        {'label': _l('Radiology Dashboard', 'لوحة الأشعة'), 'url': '/radiology/dashboard', 'icon': 'fa-x-ray'},
                        {'label': _l('Worklist', 'قائمة العمل'), 'url': '/radiology/orders', 'icon': 'fa-list-check'},
                        {'label': _l('Dose Dashboard', 'لوحة الجرعة'), 'url': '/radiology/dose-dashboard', 'icon': 'fa-radiation'},
                        {'label': _l('Critical Findings', 'النتائج الحرجة'), 'url': '/radiology/critical-findings', 'icon': 'fa-exclamation-circle'},
                        {'label': _l('Protocols', 'البروتوكولات'), 'url': '/radiology/protocols', 'icon': 'fa-clipboard-list'},
                    ]},
                ]

            if 'RadiologyTechnician' in role_set:
                items += [
                    {'section': _l('RADIOLOGY', 'الأشعة'), 'items': [
                        {'label': _l('Radiology Dashboard', 'لوحة الأشعة'), 'url': '/radiology/dashboard', 'icon': 'fa-x-ray'},
                        {'label': _l('Study Worklist', 'قائمة الدراسات'), 'url': '/radiology/orders', 'icon': 'fa-list-check'},
                        {'label': _l('Dose Dashboard', 'لوحة الجرعة'), 'url': '/radiology/dose-dashboard', 'icon': 'fa-radiation'},
                        {'label': _l('Protocols', 'البروتوكولات'), 'url': '/radiology/protocols', 'icon': 'fa-clipboard-list'},
                    ]},
                ]

            if 'Pharmacist' in role_set:
                items += [
                    {'section': _l('PHARMACY', 'الصيدلية'), 'items': [
                        {'label': _l('Prescription Queue', 'صف الصرف'), 'url': '/pharmacy/dashboard', 'icon': 'fa-prescription'},
                        {'label': _l('Inventory', 'المخزون'), 'url': '/pharmacy/inventory', 'icon': 'fa-boxes-stacked'},
                        {'label': _l('Reconciliation', 'التوفيق الدوائي'), 'url': '/pharmacy/reconciliations', 'icon': 'fa-list-check'},
                        {'label': _l('Interventions', 'التدخلات'), 'url': '/pharmacy/interventions', 'icon': 'fa-handshake-angle'},
                        {'label': _l('Clinical Pharmacist AI', 'الصيدلاني السريري'), 'url': '/pharmacy/ai-workbench', 'icon': 'fa-user-doctor'},
                        {'label': _l('Interaction Check', 'فحص التفاعلات'), 'url': '/pharmacy/drug-check', 'icon': 'fa-dna'},
                    ]},
                ]

            if 'Nurse' in role_set:
                items += [
                    {'section': _l('NURSING', 'التمريض'), 'items': [
                        {'label': _l('My Patients', 'مرضاي'), 'url': '/nursing/dashboard', 'icon': 'fa-user-nurse'},
                        {'label': _l('Patient Registry', 'سجل المرضى'), 'url': '/nursing/patients', 'icon': 'fa-heartbeat'},
                        {'label': _l('Medication Schedule', 'جدول الأدوية'), 'url': '/nursing/medication-schedule', 'icon': 'fa-pills'},
                        {'label': _l('Admissions', 'الاستشفاء'), 'url': '/admissions/dashboard', 'icon': 'fa-door-open'},
                    ]},
                ]

            if 'Receptionist' in role_set:
                items += [
                    {'section': _l('RECEPTION', 'الاستقبال'), 'items': [
                        {'label': _l('Front Desk', 'مكتب الاستقبال'), 'url': '/reception/dashboard', 'icon': 'fa-concierge-bell'},
                        {'label': _l('Register Patient', 'تسجيل مريض'), 'url': '/reception/register', 'icon': 'fa-user-plus'},
                        {'label': _l('Book Appointment', 'حجز موعد'), 'url': '/reception/appointments/book', 'icon': 'fa-calendar-plus'},
                        {'label': _l('Appointments', 'المواعيد'), 'url': '/reception/appointments', 'icon': 'fa-calendar-check'},
                        {'label': _l('Waiting Queue', 'قائمة الانتظار'), 'url': '/reception/queue', 'icon': 'fa-clipboard-check'},
                        {'label': _l('Admissions', 'الاستشفاء'), 'url': '/admissions/dashboard', 'icon': 'fa-door-open'},
                        {'label': _l('Billing', 'الفواتير'), 'url': '/billing/dashboard', 'icon': 'fa-file-invoice-dollar'},
                    ]},
                ]

            if 'Cashier' in role_set:
                items += [
                    {'section': _l('FINANCE', 'المالية'), 'items': [
                        {'label': _l('Cashier Desk', 'مكتب الصندوق'), 'url': '/billing/dashboard', 'icon': 'fa-cash-register'},
                        {'label': _l('Invoices', 'الفواتير'), 'url': '/billing/bills', 'icon': 'fa-file-invoice'},
                        {'label': _l('Revenue Report', 'تقرير الإيرادات'), 'url': '/billing/reports', 'icon': 'fa-chart-line'},
                    ]},
                ]

            if 'Admin' in role_set and 'SuperAdmin' not in role_set:
                items += [
                    {'section': _l('CLINICAL', 'سريري'), 'items': [
                        {'label': _l('Patients', 'المرضى'), 'url': '/doctor/patients', 'icon': 'fa-user-injured'},
                        {'label': _l('Clinical Workbench', 'منصة سريرية'), 'url': '/clinical', 'icon': 'fa-stethoscope'},
                        {'label': _l('Clinical Alerts', 'التنبيهات السريرية'), 'url': '/clinical/alerts', 'icon': 'fa-bell'},
                    ]},
                    {'section': _l('OPERATIONS', 'العمليات'), 'items': [
                        {'label': _l('Appointments', 'المواعيد'), 'url': '/reception/appointments', 'icon': 'fa-calendar-check'},
                        {'label': _l('Task Queue', 'قائمة المهام'), 'url': '/tasks/queue', 'icon': 'fa-clipboard-list'},
                        {'label': _l('Admissions', 'الاستشفاء'), 'url': '/admissions/dashboard', 'icon': 'fa-door-open'},
                        {'label': _l('Billing', 'الفواتير'), 'url': '/billing/dashboard', 'icon': 'fa-file-invoice-dollar'},
                    ]},
                    {'section': _l('ADMINISTRATION', 'الإدارة'), 'items': [
                        {'label': _l('Users', 'المستخدمون'), 'url': '/admin/staff', 'icon': 'fa-users-cog'},
                        {'label': _l('Departments', 'الأقسام'), 'url': '/admin/departments', 'icon': 'fa-building'},
                        {'label': _l('Doctors', 'الأطباء'), 'url': '/admin/doctors', 'icon': 'fa-user-md'},
                        {'label': _l('Capacity & No-shows', 'السعة والغياب'), 'url': '/admin/capacity', 'icon': 'fa-gauge-high'},
                        {'label': _l('Reports', 'التقارير'), 'url': '/reports/', 'icon': 'fa-chart-pie'},
                        {'label': _l('Dose Reference Levels', 'عتبات الجرعة المرجعية'), 'url': '/radiology/reference-levels', 'icon': 'fa-ruler'},
                    ]},
                ]

            if 'Dentist' in role_set:
                items += [
                    {'section': _l('DENTISTRY', 'الأسنان'), 'items': [
                        {'label': _l('Dental Dashboard', 'لوحة الأسنان'), 'url': '/dentistry/dashboard', 'icon': 'fa-tooth'},
                        {'label': _l('Patients', 'المرضى'), 'url': '/dentistry/patients', 'icon': 'fa-user-injured'},
                        {'label': _l('Orthodontic Cases', 'حالات التقويم'), 'url': '/dentistry/ortho', 'icon': 'fa-teeth'},
                        {'label': _l('Referrals', 'الإحالات'), 'url': '/care/referrals', 'icon': 'fa-share-nodes'},
                    ]},
                ]

            if 'Physiotherapist' in role_set:
                items += [
                    {'section': _l('PHYSIOTHERAPY', 'العلاج الطبيعي'), 'items': [
                        {'label': _l('Rehab Dashboard', 'لوحة التأهيل'), 'url': '/physiotherapy/dashboard', 'icon': 'fa-person-walking'},
                        {'label': _l('Patients', 'المرضى'), 'url': '/physiotherapy/patients', 'icon': 'fa-user-injured'},
                        {'label': _l('Exercise Library', 'مكتبة التمارين'), 'url': '/physiotherapy/exercise-library', 'icon': 'fa-dumbbell'},
                        {'label': _l('Referrals', 'الإحالات'), 'url': '/care/referrals', 'icon': 'fa-share-nodes'},
                    ]},
                ]

            # Shared items for clinical staff are filtered by the same
            # permissions that protect their destinations.
            if role_set & {'Doctor', 'Nurse', 'LabTechnician', 'Radiologist', 'RadiologyTechnician',
                           'Pharmacist', 'Dentist', 'Physiotherapist', 'Receptionist', 'Cashier'}:
                work_items = [
                    {'label': _l('My Tasks', 'مهامي'), 'url': '/tasks/my-tasks', 'icon': 'fa-clipboard-list'},
                    {'label': _l('Department Queue', 'قائمة القسم'), 'url': '/tasks/queue', 'icon': 'fa-layer-group'},
                ]
                if role_set & {'Doctor', 'Nurse', 'Physiotherapist', 'Dentist', 'LabTechnician', 'Radiologist', 'Pharmacist'}:
                    work_items.append({'label': _l('Multidisciplinary Cases', 'الحالات متعددة التخصصات'), 'url': '/care/cases', 'icon': 'fa-people-group'})
                permissioned_links = [
                    ('TIMELINE_VIEW', 'Clinical Workbench',
                     'منصة سريرية', '/clinical', 'fa-stethoscope'),
                    ('ALERT_VIEW', 'Clinical Alerts',
                     'التنبيهات السريرية', '/clinical/alerts', 'fa-bell'),
                    ('INBOX_VIEW', 'Clinical Inbox',
                     'الصندوق السريري', '/clinical/inbox', 'fa-inbox'),
                    ('REMINDER_VIEW', 'Reminders',
                     'التذكيرات', '/clinical/reminders', 'fa-bell'),
                    ('REMINDER_VIEW', 'Recall Board',
                     'لوحة الاستدعاء', '/clinical/recall-board', 'fa-calendar-check'),
                    ('ORDER_SET_VIEW', 'Order Sets',
                     'حزم الأوامر', '/clinical/order-sets', 'fa-layer-group'),
                    ('TEMPLATE_VIEW', 'Clinical Templates',
                     'القوالب السريرية', '/clinical/templates', 'fa-clipboard-list'),
                ]
                for permission, label, label_ar, url, icon in permissioned_links:
                    if _has_menu_permission(permission):
                        work_items.append({
                            'label': _l(label, label_ar),
                            'url': url,
                            'icon': icon,
                        })
                items.append({'section': _l('WORK', 'العمل'), 'items': work_items})

        # AI TOOLS — every tool the effective roles can reach (zero-argument routes).
        ai_roles = role_set
        if is_superadmin_real and not is_previewing:
            ai_roles = set(ROLE_LABELS)
        ai_items = _ai_tools(ai_roles)
        if ai_items:
            items.append({'section': _l('AI TOOLS', 'أدوات الذكاء'), 'items': ai_items})

        # Merge role-specific groups into a small, stable set of categories.
        # This keeps multi-role users from seeing the same workflow under
        # several headings while preserving every distinct AI application.
        category_map = {
            'COMMAND CENTER': ('command', 'COMMAND CENTER', 'مركز الأوامر'),
            'CLINICAL': ('clinical', 'CLINICAL', 'سريري'),
            'PRACTICE': ('clinical', 'CLINICAL', 'سريري'),
            'WORK': ('work', 'WORK', 'العمل'),
            'OPERATIONS': ('operations', 'OPERATIONS', 'العمليات'),
            'RECEPTION': ('operations', 'OPERATIONS', 'العمليات'),
            'DIAGNOSTICS': ('diagnostics', 'DIAGNOSTICS', 'التشخيص'),
            'LABORATORY': ('diagnostics', 'DIAGNOSTICS', 'التشخيص'),
            'RADIOLOGY': ('diagnostics', 'DIAGNOSTICS', 'التشخيص'),
            'MEDICATION': ('medications', 'MEDICATIONS', 'الأدوية'),
            'PHARMACY': ('medications', 'MEDICATIONS', 'الأدوية'),
            'SPECIALTIES': ('specialties', 'SPECIALTIES', 'التخصصات'),
            'NURSING': ('specialties', 'SPECIALTIES', 'التخصصات'),
            'DENTISTRY': ('specialties', 'SPECIALTIES', 'التخصصات'),
            'PHYSIOTHERAPY': ('specialties', 'SPECIALTIES', 'التخصصات'),
            'FINANCE': ('finance', 'FINANCE', 'المالية'),
            'ADMINISTRATION': ('administration', 'ADMINISTRATION', 'الإدارة'),
            'MY HEALTH': ('health', 'MY HEALTH', 'صحتي'),
            'AI TOOLS': ('ai', 'AI TOOLS', 'أدوات الذكاء'),
        }
        category_order = [
            'command', 'health', 'clinical', 'operations', 'diagnostics',
            'medications', 'specialties', 'work', 'finance', 'administration', 'ai',
        ]
        seen_urls = set()
        grouped = {}
        for group in items:
            if not group.get('items'):
                continue
            section = str(group.get('section', ''))
            category = category_map.get(section)
            if category is None:
                category = (section, section, section)
            key, en_label, ar_label = category
            target = grouped.setdefault(key, {
                'section': _l(en_label, ar_label), 'key': key, 'items': []})
            for item in group['items']:
                identity = item['url']
                if identity in seen_urls:
                    continue
                seen_urls.add(identity)
                target['items'].append(item)
        compact_items = [grouped[key] for key in category_order if key in grouped]
        compact_items.extend(
            group for key, group in grouped.items() if key not in category_order)
        return compact_items

    def _is_superadmin():
        from flask_login import current_user
        return current_user.is_authenticated and current_user.has_role('SuperAdmin')

    def _is_previewing():
        return bool(session.get('preview_role'))

    def _effective_role_name():
        from flask_login import current_user
        if session.get('preview_role'):
            return ROLE_LABELS.get(session.get('preview_role'), '')
        if current_user.is_authenticated and current_user.roles:
            return ROLE_LABELS.get(current_user.roles[0].name, current_user.roles[0].name)
        return ''

    def ai_quick_tools(limit=4):
        """Up to ``limit`` AI capabilities for the effective role, for the
        dashboard AI strip. Uses the AI Hub catalogue; never calls the provider."""
        from flask_login import current_user
        if not current_user.is_authenticated:
            return []
        try:
            from app.services.ai import hub as hub_svc
            data = hub_svc.build(_get_effective_roles(current_user), has_permission=current_user.has_permission)
        except Exception:  # noqa: BLE001 - a dashboard must render without AI
            return []
        out = []
        for grp in data['groups']:
            if grp['key'] == 'copilot':
                continue
            for it in grp['items']:
                if it.get('url'):
                    out.append(it)
        return out[:limit]

    MODEL_ENDPOINTS = ('/ai/chest-xray', '/ai/fracture-detection', '/ai/tooth-segmentation', '/ai/skin-lesion-detection', '/ai/ich-detection')

    def ai_models_block():
        """The five local imaging models (chest, fracture, tooth, skin, head-CT haemorrhage) the effective
        role may open, in that fixed order, plus the other AI tools as a short list.
        Uses the AI Hub catalogue; never calls the provider."""
        from flask_login import current_user
        empty = {'models': [], 'others': []}
        if not current_user.is_authenticated:
            return empty
        try:
            from app.services.ai import hub as hub_svc
            data = hub_svc.build(_get_effective_roles(current_user), has_permission=current_user.has_permission)
        except Exception:  # noqa: BLE001 - a dashboard must render without AI
            return empty
        models, others, seen = {}, [], set()
        for grp in data['groups']:
            if grp['key'] == 'copilot':
                continue
            for it in grp['items']:
                url = it.get('url') or ''
                if not url or url in seen:
                    continue
                seen.add(url)
                path = url.split('?')[0]
                if path in MODEL_ENDPOINTS:
                    models[path] = it
                else:
                    others.append(it)
        # Canonical names + one-line descriptions for the four models (the hub
        # catalogue groups the skin model under "Dermatology AI").
        canon = {
            '/ai/chest-xray': ('Chest X-ray Screening', 'فحص أشعة الصدر',
                               'Frontal chest X-ray: 18 findings; confident critical ones raise an alert.',
                               'أشعة الصدر الأمامية: 18 نتيجة، والحرجة الواثقة تُنشئ تنبيهًا.'),
            '/ai/fracture-detection': ('Fracture Detection', 'كشف الكسور',
                                       'Bone X-ray fracture detection with an annotated image.',
                                       'كشف الكسور في أشعة العظام مع صورة موضّحة.'),
            '/ai/tooth-segmentation': ('Tooth Segmentation', 'تجزئة الأسنان',
                                       'Panoramic X-ray tooth segmentation mask for dental planning.',
                                       'قناع تجزئة الأسنان من الأشعة البانورامية للتخطيط السني.'),
            '/ai/skin-lesion-detection': ('Skin Lesion Detection', 'كشف آفات الجلد',
                                          'Melanoma vs. nevus on a lesion photo, with a Grad-CAM heatmap.',
                                          'ميلانوما أم وحمة من صورة الآفة، مع خريطة انتباه Grad-CAM.'),
            '/ai/ich-detection': ('Head CT Haemorrhage Detection', 'كشف النزف الدماغي (CT)',
                                  'Intracranial haemorrhage on head CT slices or a whole series, with LayerCAM.',
                                  'نزف داخل الجمجمة في مقاطع CT الرأس أو سلسلة كاملة، مع LayerCAM.'),
        }
        ordered = []
        for e in MODEL_ENDPOINTS:
            if e in models:
                it = dict(models[e])
                it['label'], it['label_ar'], it['desc'], it['desc_ar'] = canon[e]
                ordered.append(it)
        return {'models': ordered, 'others': others[:6]}

    from datetime import date as _date
    app.context_processor(lambda: {
        'today': _date.today(),
        'current_user_menus': menus,
        'ai_quick_tools': ai_quick_tools,
        'ai_models_block': ai_models_block,
        'is_superadmin_real': _is_superadmin,
        'is_previewing': _is_previewing,
        'preview_role': lambda: session.get('preview_role'),
        'effective_role_name': _effective_role_name,
    })

    def unread_count():
        from flask_login import current_user
        if not current_user.is_authenticated:
            return 0
        from app.models import Notification
        return Notification.query.filter_by(
            user_id=current_user.id, is_read=False).count()

    app.context_processor(lambda: {'unread_notifications': unread_count()})

    def pending_task_count():
        from flask_login import current_user
        if not current_user.is_authenticated:
            return 0
        from app.models import Task
        role_names = [r.name for r in current_user.roles]
        return Task.query.filter(
            db.or_(Task.assigned_to == current_user.id,
                   db.and_(Task.assigned_to.is_(None), Task.assigned_role.in_(role_names or ['-']))),
            Task.status.in_(['NEW', 'ASSIGNED', 'IN_PROGRESS'])
        ).count()

    app.context_processor(lambda: {'pending_tasks': pending_task_count()})

    STAFF_AI_ROLES = {'Doctor', 'Nurse', 'Pharmacist', 'Dentist', 'Physiotherapist',
                      'Radiologist', 'RadiologyTechnician', 'Admin', 'SuperAdmin'}

    def copilot_context():
        """One AI entry point + immediate critical-alert visibility. Computed
        once per request; never calls the AI provider."""
        from flask import session
        from flask_login import current_user
        empty = {'copilot_enabled': False, 'ai_status': None, 'copilot_critical_alerts': [], 'dictation_enabled': False}
        if not current_user.is_authenticated:
            return empty
        # Cached on the request object (not ``g``: under a long-lived app
        # context, e.g. the test client, ``g`` would leak between requests).
        from flask import request
        cached = getattr(request, '_copilot_ctx', None)
        if cached is not None:
            return cached
        roles = {r.name for r in current_user.roles}
        preview = session.get('preview_role')
        if preview:
            roles = {preview}
        enabled = bool(roles & STAFF_AI_ROLES) and current_user.has_permission('AI_USE')
        ai_status = None
        if enabled:
            try:
                from app.services.ai.platform import status as ai_status_fn
                ai_status = ai_status_fn()
            except Exception:  # noqa: BLE001 - never break a page for the pill
                ai_status = None
        critical = []
        if enabled and roles & {'Doctor', 'Admin', 'SuperAdmin'}:
            try:
                from app.models import ClinicalAlert
                # The red banner is reserved for radiology critical findings (signed
                # report -> engine -> alert). Other rule alerts stay in the safety lists.
                q = ClinicalAlert.query.filter(ClinicalAlert.status == 'OPEN',
                                               ClinicalAlert.severity.in_(('CRITICAL', 'HIGH')),
                                               ClinicalAlert.source_type == 'radiology_report')
                if not roles & {'Admin', 'SuperAdmin'}:
                    q = q.filter(ClinicalAlert.assigned_to == current_user.id)
                critical = q.order_by(ClinicalAlert.created_at.desc()).limit(5).all()
            except Exception:  # noqa: BLE001
                critical = []
        try:
            from app.services.ai.dictation import dictation_available
            dictation = enabled and dictation_available()
        except Exception:  # noqa: BLE001
            dictation = False
        request._copilot_ctx = {'copilot_enabled': enabled, 'ai_status': ai_status,
                                'copilot_critical_alerts': critical, 'dictation_enabled': dictation}
        return request._copilot_ctx

    app.context_processor(copilot_context)

    @app.before_request
    def set_language():
        lang = request.args.get('lang')
        if lang in ('en', 'ar'):
            session['lang'] = lang
        g.lang = session.get('lang', 'en')
        g.theme = session.get('theme', 'light')

    app.context_processor(lambda: {'g': g})

    from app.services import clinical_scores as _scores
    app.jinja_env.globals.update(news2_score=_scores.news2, qsofa_score=_scores.qsofa,
                                 lace_for_admission=_scores.lace_for_admission)


def register_error_handlers(app):
    """Production-safe error handlers. Never expose stack traces, SQL, or secrets."""

    from flask import render_template, request, jsonify

    def _wants_json():
        return request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json'

    @app.errorhandler(400)
    def bad_request(e):
        if _wants_json():
            return jsonify({'error': 'Bad request'}), 400
        return render_template('errors/400.html'), 400

    @app.errorhandler(401)
    def unauthorized(e):
        if _wants_json():
            return jsonify({'error': 'Unauthorized'}), 401
        return render_template('errors/401.html'), 401

    @app.errorhandler(403)
    def forbidden(e):
        if _wants_json():
            return jsonify({'error': 'Forbidden'}), 403
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        if _wants_json():
            return jsonify({'error': 'Not found'}), 404
        return render_template('errors/404.html'), 404

    @app.errorhandler(409)
    def conflict(e):
        if _wants_json():
            return jsonify({'error': 'Conflict'}), 409
        return render_template('errors/400.html'), 409

    @app.errorhandler(429)
    def rate_limited(e):
        if _wants_json():
            return jsonify({'error': 'Too many requests'}), 429
        return render_template('errors/400.html'), 429

    @app.errorhandler(422)
    def unprocessable(e):
        if _wants_json():
            return jsonify({'error': 'Unprocessable entity'}), 422
        return render_template('errors/422.html'), 422

    @app.errorhandler(500)
    def internal_error(e):
        # Roll back any broken session to keep the connection healthy.
        from app import db
        db.session.rollback()
        if _wants_json():
            return jsonify({'error': 'Internal server error'}), 500
        return render_template('errors/500.html'), 500

    @app.errorhandler(503)
    def service_unavailable(e):
        if _wants_json():
            return jsonify({'error': 'Service unavailable'}), 503
        return render_template('errors/500.html'), 503
