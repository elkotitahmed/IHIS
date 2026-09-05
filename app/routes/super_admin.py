"""Super Admin portal - Executive command center with global visibility."""
import os
import shutil
from datetime import timedelta

from flask import (
    Blueprint, request, redirect, url_for, flash, render_template,
    abort, current_app, session, jsonify,
)
from flask_login import login_required, current_user

from app import db
from app.models import (
    User, Role, Permission, AuditLog, SystemSetting, Patient, Doctor,
    Department, Appointment, LabOrder, LabResult, RadiologyOrder,
    RadiologyReport, Prescription, Bill, Payment, Task, Notification,
    Admission, Referral, VitalSign, DentalRecord, TherapySession,
    DispensingRecord, PharmacyInventory, PharmacyIntervention,
    ClinicalAlert, TimelineEvent,
)
from app.routes.decorators import roles_required, permissions_required, log_activity
from app.permissions import ADMIN_MANAGE_PERMISSIONS, ADMIN_MANAGE_ROLES, ADMIN_MANAGE_USERS
from app.utils import utcnow

super_admin_bp = Blueprint('super_admin', __name__)

DASHBOARD_ACTIONS = (
    "LOGIN_FAILED",
    "LOGIN_FAILURE",
    "AUTH_FAILED",
    "PASSWORD_RESET_FAILED",
)

ROLE_LABELS = {
    'SuperAdmin': 'Super Administrator',
    'Admin': 'Administrator',
    'Doctor': 'Doctor',
    'Nurse': 'Nurse',
    'LabTechnician': 'Lab Technician',
    'Radiologist': 'Radiologist',
    'Pharmacist': 'Pharmacist',
    'Receptionist': 'Receptionist',
    'Dentist': 'Dentist',
    'Physiotherapist': 'Physiotherapist',
    'Cashier': 'Cashier',
    'Patient': 'Patient',
}


# ---------------------------------------------------------------------------
# EXECUTIVE DASHBOARD — Hospital Operations Cockpit
# ---------------------------------------------------------------------------
@super_admin_bp.route('/dashboard')
@login_required
@roles_required('SuperAdmin')
def dashboard():
    now = utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    # --- System Health ---
    error_log_count = AuditLog.query.filter(
        AuditLog.action.in_(DASHBOARD_ACTIONS),
        AuditLog.created_at >= seven_days_ago,
    ).count()
    system_health = ('Degraded' if error_log_count > 20
                     else 'Warning' if error_log_count > 0
                     else 'Operational')

    # --- User Metrics ---
    active_users = User.query.filter_by(is_active=True).count()
    total_users = User.query.count()
    recent_logins = User.query.filter(
        User.last_login.isnot(None),
        User.last_login >= seven_days_ago,
    ).count()

    # --- User Distribution by Role ---
    role_counts = {}
    for role_name in ROLE_LABELS:
        count = User.query.join(User.roles).filter(Role.name == role_name).count()
        if count > 0:
            role_counts[role_name] = count

    # --- Patient Metrics ---
    total_patients = Patient.query.count()
    total_doctors = Doctor.query.count()

    # --- Today's Operations ---
    today_appts = Appointment.query.filter(
        Appointment.scheduled_at >= today_start,
        Appointment.scheduled_at < today_start + timedelta(days=1),
    )
    appts_today = today_appts.count()
    appts_waiting = today_appts.filter(
        Appointment.status.in_(['Scheduled', 'CheckedIn'])
    ).count()

    # --- Encounters / Consultations ---
    active_encounters = today_appts.filter(
        Appointment.status == 'InConsultation'
    ).count()

    # --- Lab Operations ---
    pending_lab = LabOrder.query.filter(
        LabOrder.status.in_(['Pending', 'ORDERED', 'ACCEPTED', 'COLLECTED', 'PROCESSING'])
    ).count()
    completed_lab_today = LabOrder.query.filter(
        LabOrder.status.in_(['RESULTED', 'VERIFIED', 'FINALIZED']),
        LabOrder.order_date >= today_start,
    ).count()
    lab_volume_total = LabOrder.query.count()

    # --- Radiology Operations ---
    pending_radiology = RadiologyOrder.query.filter(
        RadiologyOrder.status.in_(['Pending', 'ORDERED', 'SCHEDULED', 'ARRIVED', 'IN_PROGRESS'])
    ).count()
    completed_radiology_today = RadiologyOrder.query.filter(
        RadiologyOrder.status.in_(['PERFORMED', 'REPORTED', 'SIGNED', 'FINALIZED']),
        RadiologyOrder.order_date >= today_start,
    ).count()
    radiology_volume_total = RadiologyOrder.query.count()

    # --- Prescription / Pharmacy ---
    pending_prescriptions = Prescription.query.filter(
        Prescription.status.in_(['Active', 'Pending'])
    ).count()
    dispensed_today = DispensingRecord.query.filter(
        DispensingRecord.dispensed_at >= today_start,
    ).count() if hasattr(DispensingRecord, 'dispensed_at') else 0
    pharmacy_items = PharmacyInventory.query.filter(
        PharmacyInventory.current_stock > 0
    ).count() if hasattr(PharmacyInventory, 'current_stock') else 0

    # --- Nursing ---
    vitals_today = VitalSign.query.filter(
        VitalSign.recorded_at >= today_start,
    ).count() if hasattr(VitalSign, 'recorded_at') else 0

    # --- Billing ---
    today_bills = Bill.query.filter(
        Bill.issued_at >= today_start,
    ).count()
    today_payments = Payment.query.filter(
        Payment.received_at >= today_start,
    ).count()
    daily_revenue = db.session.query(
        db.func.coalesce(db.func.sum(Payment.amount), 0.0)
    ).filter(Payment.received_at >= today_start).scalar() or 0.0

    total_bills = Bill.query.count()
    unpaid_bills = Bill.query.filter(
        Bill.status.in_(['Unpaid', 'PartiallyPaid'])
    ).count()
    # Sum outstanding balance in Python since Bill.total() is computed
    all_unpaid = Bill.query.filter(
        Bill.status.in_(['Unpaid', 'PartiallyPaid'])
    ).all()
    total_outstanding = round(sum(b.total() for b in all_unpaid), 2)

    total_revenue = db.session.query(
        db.func.coalesce(db.func.sum(Payment.amount), 0.0)
    ).scalar() or 0.0

    # --- Tasks ---
    total_tasks = Task.query.count()
    pending_tasks = Task.query.filter(
        Task.status.in_(['NEW', 'ASSIGNED', 'IN_PROGRESS'])
    ).count()
    overdue_tasks = Task.query.filter(
        Task.status.in_(['NEW', 'ASSIGNED', 'IN_PROGRESS']),
        Task.due_at < now,
    ).count() if hasattr(Task, 'due_at') else 0

    # --- Notifications ---
    unread_notifications_total = Notification.query.filter_by(is_read=False).count()

    # --- Alerts ---
    open_alerts = ClinicalAlert.query.filter(
        ClinicalAlert.status == 'OPEN'
    ).count() if hasattr(ClinicalAlert, 'status') else 0

    # --- Admissions & Discharges ---
    active_admissions = Admission.query.filter(
        Admission.status == 'Active'
    ).count() if hasattr(Admission, 'status') else 0
    admissions_today = Admission.query.filter(
        Admission.admission_date >= today_start
    ).count() if hasattr(Admission, 'admission_date') else 0
    discharges_today = Admission.query.filter(
        Admission.discharge_date >= today_start
    ).count() if hasattr(Admission, 'discharge_date') else 0

    # --- Referrals ---
    pending_referrals = Referral.query.filter(
        Referral.status.in_(['Pending', 'Active'])
    ).count() if hasattr(Referral, 'status') else 0

    # --- Dental ---
    dental_records = DentalRecord.query.count() if hasattr(DentalRecord, 'id') else 0

    # --- Physiotherapy ---
    therapy_sessions = TherapySession.query.filter(
        TherapySession.session_date >= today_start
    ).count() if hasattr(TherapySession, 'session_date') else 0

    # --- Security ---
    security_alerts = AuditLog.query.filter(
        AuditLog.action.in_(DASHBOARD_ACTIONS)
    ).count()
    failed_logins_7d = AuditLog.query.filter(
        AuditLog.action.in_(DASHBOARD_ACTIONS),
        AuditLog.created_at >= seven_days_ago,
    ).count()

    # --- Audit Activity ---
    total_audit_logs = AuditLog.query.count()
    recent_audits = AuditLog.query.order_by(
        AuditLog.created_at.desc()
    ).limit(15).all()

    # --- Departments ---
    total_roles = Role.query.count()
    total_permissions = Permission.query.count()
    total_settings = SystemSetting.query.count()

    # --- Recent Activity Feed (Cross-Department) ---
    recent_tasks = Task.query.order_by(Task.created_at.desc()).limit(10).all()

    return render_template(
        'super_admin/dashboard.html',
        title='Command Center',
        now=now,
        # System
        system_health=system_health,
        error_log_count=error_log_count,
        total_roles=total_roles,
        total_permissions=total_permissions,
        total_audit_logs=total_audit_logs,
        total_settings=total_settings,
        # Users
        active_users=active_users,
        total_users=total_users,
        recent_logins=recent_logins,
        role_counts=role_counts,
        # Patients
        total_patients=total_patients,
        total_doctors=total_doctors,
        # Today
        appts_today=appts_today,
        appts_waiting=appts_waiting,
        active_encounters=active_encounters,
        # Lab
        pending_lab=pending_lab,
        completed_lab_today=completed_lab_today,
        lab_volume_total=lab_volume_total,
        # Radiology
        pending_radiology=pending_radiology,
        completed_radiology_today=completed_radiology_today,
        radiology_volume_total=radiology_volume_total,
        # Pharmacy
        pending_prescriptions=pending_prescriptions,
        dispensed_today=dispensed_today,
        pharmacy_items=pharmacy_items,
        # Nursing
        vitals_today=vitals_today,
        # Billing
        today_bills=today_bills,
        today_payments=today_payments,
        daily_revenue=daily_revenue,
        total_bills=total_bills,
        unpaid_bills=unpaid_bills,
        total_outstanding=total_outstanding,
        total_revenue=total_revenue,
        # Tasks
        total_tasks=total_tasks,
        pending_tasks=pending_tasks,
        overdue_tasks=overdue_tasks,
        # Notifications
        unread_notifications_total=unread_notifications_total,
        # Alerts
        open_alerts=open_alerts,
        # Admissions
        active_admissions=active_admissions,
        admissions_today=admissions_today,
        discharges_today=discharges_today,
        # Referrals
        pending_referrals=pending_referrals,
        # Specialty
        dental_records=dental_records,
        therapy_sessions=therapy_sessions,
        # Security
        security_alerts=security_alerts,
        failed_logins_7d=failed_logins_7d,
        # Activity
        recent_audits=recent_audits,
        recent_tasks=recent_tasks,
        role_labels=ROLE_LABELS,
    )


# ---------------------------------------------------------------------------
# PLATFORM CAPABILITIES — Feature Discovery Center
# ---------------------------------------------------------------------------
@super_admin_bp.route('/capabilities')
@login_required
@roles_required('SuperAdmin')
def capabilities():
    capabilities_data = [
        {
            'group': 'Clinical',
            'icon': 'fa-stethoscope',
            'color': 'var(--ihis-primary)',
            'items': [
                {'feature': 'Patient Management', 'desc': 'Register, search, and manage patients with MRN tracking',
                 'roles': 'Receptionist, Admin, Doctor, Nurse', 'url': '/reception/dashboard'},
                {'feature': 'Patient 360', 'desc': 'Complete clinical view with timeline, alerts, and cross-department context',
                 'roles': 'All clinical staff', 'url': '/clinical'},
                {'feature': 'Encounters & EMR', 'desc': 'Clinical encounters with diagnosis, orders, prescriptions',
                 'roles': 'Doctor, Admin', 'url': '/doctor/patients'},
                {'feature': 'Clinical Timeline', 'desc': 'Cross-department event timeline per patient',
                 'roles': 'All clinical staff', 'url': '/clinical'},
                {'feature': 'Clinical Alerts', 'desc': 'Automated clinical alert engine with severity lifecycle',
                 'roles': 'All clinical staff', 'url': '/clinical/alerts'},
                {'feature': 'Allergies & Problems', 'desc': 'Structured allergy and problem list management',
                 'roles': 'Doctor, Nurse, Pharmacist', 'url': '/clinical'},
                {'feature': 'Follow-ups', 'desc': 'Schedule and track patient follow-up visits',
                 'roles': 'Doctor, Nurse', 'url': '/clinical'},
                {'feature': 'Care Teams', 'desc': 'Multidisciplinary care team coordination',
                 'roles': 'Doctor, Admin', 'url': '/care/team'},
            ],
        },
        {
            'group': 'Diagnostics',
            'icon': 'fa-flask',
            'color': 'var(--ihis-info)',
            'items': [
                {'feature': 'Lab Orders', 'desc': 'Order tests, track specimens, enter and verify results',
                 'roles': 'Doctor, Lab Technician', 'url': '/lab/orders'},
                {'feature': 'Lab Result Verification', 'desc': 'Verify and finalize lab results with critical value handling',
                 'roles': 'Lab Technician', 'url': '/lab/orders'},
                {'feature': 'Radiology Orders', 'desc': 'Order imaging, schedule studies, perform and report',
                 'roles': 'Doctor, Radiologist', 'url': '/radiology/orders'},
                {'feature': 'Radiology Reporting', 'desc': 'Findings, impression, recommendation, and sign-off',
                 'roles': 'Radiologist', 'url': '/radiology/orders'},
                {'feature': 'Test Catalog', 'desc': 'Manage lab test catalog with pricing',
                 'roles': 'Lab Technician, Admin', 'url': '/lab/catalog'},
            ],
        },
        {
            'group': 'Medication',
            'icon': 'fa-pills',
            'color': 'var(--ihis-success)',
            'items': [
                {'feature': 'Prescriptions', 'desc': 'Create, view, and manage prescriptions',
                 'roles': 'Doctor, Pharmacist', 'url': '/pharmacy/dashboard'},
                {'feature': 'Prescription Dispensing', 'desc': 'Dispense medications with batch and stock tracking',
                 'roles': 'Pharmacist', 'url': '/pharmacy/dashboard'},
                {'feature': 'Pharmacy Inventory', 'desc': 'Stock management with batch tracking and expiry alerts',
                 'roles': 'Pharmacist, Admin', 'url': '/pharmacy/inventory'},
                {'feature': 'Drug Interaction Check', 'desc': 'Check drug-drug and drug-allergy interactions',
                 'roles': 'Doctor, Pharmacist', 'url': '/pharmacy/drug-check'},
                {'feature': 'Medication Reconciliation', 'desc': 'Reconcile medications at transitions of care',
                 'roles': 'Pharmacist', 'url': '/pharmacy/reconciliations'},
                {'feature': 'Clinical Pharmacy AI', 'desc': 'AI-powered medication therapy review',
                 'roles': 'Pharmacist', 'url': '/pharmacy/ai-workbench'},
                {'feature': 'Pharmacy Interventions', 'desc': 'Document and resolve pharmacy interventions',
                 'roles': 'Pharmacist, Doctor', 'url': '/pharmacy/interventions'},
            ],
        },
        {
            'group': 'Nursing',
            'icon': 'fa-user-nurse',
            'color': 'var(--ihis-purple)',
            'items': [
                {'feature': 'Vital Signs', 'desc': 'Record and track patient vital signs',
                 'roles': 'Nurse', 'url': '/nursing/patients'},
                {'feature': 'Nursing Notes', 'desc': 'Clinical nursing documentation',
                 'roles': 'Nurse', 'url': '/nursing/notes'},
                {'feature': 'Care Plans', 'desc': 'Create and manage nursing care plans',
                 'roles': 'Nurse', 'url': '/nursing/care-plans'},
                {'feature': 'Medication Administration', 'desc': 'MAR with administration tracking',
                 'roles': 'Nurse', 'url': '/nursing/mar'},
                {'feature': 'Intake & Output', 'desc': 'Fluid balance monitoring',
                 'roles': 'Nurse', 'url': '/nursing/intake-output'},
                {'feature': 'Risk Assessment', 'desc': 'Nursing risk assessment tools',
                 'roles': 'Nurse', 'url': '/nursing'},
            ],
        },
        {
            'group': 'Rehabilitation',
            'icon': 'fa-person-walking',
            'color': 'var(--ihis-teal)',
            'items': [
                {'feature': 'Therapy Assessments', 'desc': 'Physical therapy initial and follow-up assessments',
                 'roles': 'Physiotherapist', 'url': '/physiotherapy/patients'},
                {'feature': 'Treatment Plans', 'desc': 'Rehabilitation treatment planning',
                 'roles': 'Physiotherapist', 'url': '/physiotherapy/patients'},
                {'feature': 'Therapy Sessions', 'desc': 'Session documentation and progress tracking',
                 'roles': 'Physiotherapist', 'url': '/physiotherapy/patients'},
                {'feature': 'Exercise Library', 'desc': 'Exercise catalog with instructions',
                 'roles': 'Physiotherapist', 'url': '/physiotherapy/exercise-library'},
                {'feature': 'Progress Tracking', 'desc': 'Pain, ROM, strength, and functional outcome tracking',
                 'roles': 'Physiotherapist', 'url': '/physiotherapy/patients'},
            ],
        },
        {
            'group': 'Dentistry',
            'icon': 'fa-tooth',
            'color': 'var(--ihis-amber)',
            'items': [
                {'feature': 'Dental Records', 'desc': 'Patient dental records and history',
                 'roles': 'Dentist', 'url': '/dentistry/patients'},
                {'feature': 'Odontogram', 'desc': 'Visual dental chart with tooth status',
                 'roles': 'Dentist', 'url': '/dentistry/patients'},
                {'feature': 'Treatment Plans', 'desc': 'Dental treatment planning and sequencing',
                 'roles': 'Dentist', 'url': '/dentistry/patients'},
                {'feature': 'Dental Procedures', 'desc': 'Procedure documentation',
                 'roles': 'Dentist', 'url': '/dentistry/patients'},
                {'feature': 'Dental Imaging', 'desc': 'Dental X-ray management',
                 'roles': 'Dentist', 'url': '/dentistry/imaging'},
                {'feature': 'Orthodontic Cases', 'desc': 'Orthodontic case management',
                 'roles': 'Dentist', 'url': '/dentistry/ortho'},
            ],
        },
        {
            'group': 'Operations',
            'icon': 'fa-building',
            'color': 'var(--ihis-warning)',
            'items': [
                {'feature': 'Appointments', 'desc': 'Schedule, check-in, and manage appointments',
                 'roles': 'Receptionist, Doctor, Admin', 'url': '/reception/appointments'},
                {'feature': 'Queue Management', 'desc': 'Patient check-in queue',
                 'roles': 'Receptionist', 'url': '/reception/queue'},
                {'feature': 'Admissions', 'desc': 'Patient admission and bed management',
                 'roles': 'Receptionist, Admin', 'url': '/admissions/dashboard'},
                {'feature': 'Discharge', 'desc': 'Discharge planning and summary',
                 'roles': 'Doctor, Admin', 'url': '/admissions/dashboard'},
                {'feature': 'Referrals', 'desc': 'Cross-department referral management',
                 'roles': 'Doctor, Admin', 'url': '/care/referrals'},
                {'feature': 'Task Engine', 'desc': 'Cross-department task queue and assignment',
                 'roles': 'All staff', 'url': '/tasks/my-tasks'},
            ],
        },
        {
            'group': 'Finance',
            'icon': 'fa-file-invoice-dollar',
            'color': 'var(--ihis-danger)',
            'items': [
                {'feature': 'Billing', 'desc': 'Invoice generation from clinical events',
                 'roles': 'Receptionist, Admin, Cashier', 'url': '/billing/dashboard'},
                {'feature': 'Payments', 'desc': 'Payment recording with receipt generation',
                 'roles': 'Cashier, Admin', 'url': '/billing/dashboard'},
                {'feature': 'Service Catalog', 'desc': 'Manage billable services and pricing',
                 'roles': 'Admin', 'url': '/billing/service-catalog'},
                {'feature': 'Financial Reports', 'desc': 'Revenue and billing analytics',
                 'roles': 'Admin, SuperAdmin', 'url': '/reports'},
            ],
        },
        {
            'group': 'AI',
            'icon': 'fa-robot',
            'color': 'var(--ihis-info)',
            'items': [
                {'feature': 'Clinical Summary AI', 'desc': 'AI-generated patient clinical summaries',
                 'roles': 'Doctor, Nurse, Admin', 'url': '/ai/summary'},
                {'feature': 'Diagnosis Support', 'desc': 'AI-assisted diagnosis suggestions',
                 'roles': 'Doctor', 'url': '/ai/diagnosis-support'},
                {'feature': 'Lab Interpretation', 'desc': 'AI-assisted lab result interpretation',
                 'roles': 'Doctor, Lab Technician', 'url': '/ai/lab'},
                {'feature': 'Radiology AI', 'desc': 'AI-assisted radiology analysis',
                 'roles': 'Radiologist', 'url': '/ai/radiology'},
                {'feature': 'Fracture Detection', 'desc': 'YOLOv8 bone fracture detection from X-rays',
                 'roles': 'Doctor, Radiologist', 'url': '/ai/fracture-detection'},
                {'feature': 'Tooth Segmentation', 'desc': 'U-Net dental panoramic X-ray segmentation',
                 'roles': 'Dentist', 'url': '/ai/tooth-segmentation'},
                {'feature': 'Skin Lesion Detection', 'desc': 'ResNet-50 + EfficientNet-B0 Nevus vs Melanoma classification',
                 'roles': 'Doctor', 'url': '/ai/skin-lesion-detection'},
                {'feature': 'Health Insights', 'desc': 'AI-powered patient health insights',
                 'roles': 'Doctor, Patient', 'url': '/ai/health-insights'},
                {'feature': 'Appointment Optimization', 'desc': 'AI scheduling optimization',
                 'roles': 'Admin', 'url': '/admin/ai/appointment-optimization'},
                {'feature': 'ICD-10 Coding', 'desc': 'AI-assisted medical coding',
                 'roles': 'Doctor, Admin', 'url': '/admin/ai/coding-assistant'},
            ],
        },
        {
            'group': 'Administration',
            'icon': 'fa-cog',
            'color': 'var(--ihis-text-secondary)',
            'items': [
                {'feature': 'User Management', 'desc': 'Create, activate, deactivate users',
                 'roles': 'Admin, SuperAdmin', 'url': '/admin/staff'},
                {'feature': 'Role & Permission Management', 'desc': 'Configure roles and fine-grained permissions',
                 'roles': 'SuperAdmin', 'url': '/super-admin/roles'},
                {'feature': 'Audit Logs', 'desc': 'Complete system audit trail',
                 'roles': 'SuperAdmin', 'url': '/super-admin/audit-logs'},
                {'feature': 'System Settings', 'desc': 'System-wide configuration',
                 'roles': 'SuperAdmin', 'url': '/super-admin/settings'},
                {'feature': 'Database Backup', 'desc': 'Create database backups',
                 'roles': 'SuperAdmin', 'url': '/super-admin/backup'},
                {'feature': 'Reports', 'desc': 'Operational and clinical reports',
                 'roles': 'Admin, SuperAdmin', 'url': '/reports'},
            ],
        },
    ]

    total_features = sum(len(g['items']) for g in capabilities_data)
    stats = [
        {'label': 'Clinical Features', 'count': sum(len(g['items']) for g in capabilities_data if g['group'] == 'Clinical'), 'icon': 'fa-stethoscope', 'color': 'var(--ihis-primary)'},
        {'label': 'Diagnostic Tools', 'count': sum(len(g['items']) for g in capabilities_data if g['group'] == 'Diagnostics'), 'icon': 'fa-flask', 'color': 'var(--ihis-info)'},
        {'label': 'AI Capabilities', 'count': sum(len(g['items']) for g in capabilities_data if g['group'] == 'AI'), 'icon': 'fa-robot', 'color': 'var(--ihis-info)'},
        {'label': 'Total Features', 'count': total_features, 'icon': 'fa-rocket', 'color': 'var(--ihis-success)'},
    ]

    groups = []
    for g in capabilities_data:
        groups.append({
            'label': g['group'],
            'icon': g['icon'],
            'icon_color': g['color'],
            'description': f"{len(g['items'])} features in {g['group']}",
            'capabilities': [
                {
                    'label': item['feature'],
                    'description': item['desc'],
                    'icon': g['icon'],
                    'category': g['group'],
                    'roles': [r.strip() for r in item['roles'].split(',')],
                }
                for item in g['items']
            ],
        })

    return render_template(
        'super_admin/capabilities.html',
        title='Platform Capabilities',
        groups=groups,
        stats=stats,
        total=total_features,
    )


# ---------------------------------------------------------------------------
# ROLE PREVIEW — Simulate the UI as another role
# ---------------------------------------------------------------------------
@super_admin_bp.route('/preview/<role_name>')
@login_required
@roles_required('SuperAdmin')
def preview_role(role_name):
    """Set a session flag so the base template renders the specified role's UI."""
    valid_roles = list(ROLE_LABELS.keys())
    if role_name not in valid_roles:
        abort(404)
    session['preview_role'] = role_name
    log_activity('ROLE_PREVIEW', 'System', None,
                 f'SuperAdmin previewing as {role_name}')
    db.session.commit()
    return redirect(url_for('main.dashboard'))


@super_admin_bp.route('/exit-preview')
@login_required
@roles_required('SuperAdmin')
def exit_preview():
    session.pop('preview_role', None)
    return redirect(url_for('super_admin.dashboard'))


# ---------------------------------------------------------------------------
# ORIGINAL ROUTES (preserved and enhanced)
# ---------------------------------------------------------------------------
@super_admin_bp.route('/users')
@login_required
@roles_required('SuperAdmin')
def users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('super_admin/users.html', title='Manage Users', users=users)


@super_admin_bp.route('/users/<int:user_id>/toggle', methods=['POST'])
@login_required
@roles_required('SuperAdmin')
def toggle_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404, description='User not found')
    if user.id == current_user.id:
        flash('You cannot deactivate your own account.', 'danger')
        return redirect(url_for('super_admin.users'))

    user.is_active = not user.is_active
    db.session.commit()
    status = 'activated' if user.is_active else 'deactivated'
    log_activity('TOGGLE_USER', 'User', user.id, f'{user.username}: {status}')
    db.session.commit()
    flash(f'User "{user.username}" has been {status}.', 'success')
    return redirect(url_for('super_admin.users'))


@super_admin_bp.route('/users/<int:user_id>/roles', methods=['GET', 'POST'])
@login_required
@roles_required('SuperAdmin')
def user_roles(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404, description='User not found')

    if request.method == 'POST':
        role_name = request.form.get('role_name', '').strip()
        if role_name:
            role = Role.query.filter_by(name=role_name).first()
            if role is None:
                role = Role(name=role_name, description=f'Auto-created role {role_name}')
                db.session.add(role)
                db.session.flush()
            if role not in user.roles:
                user.roles.append(role)
                db.session.commit()
                log_activity('ADD_ROLE_TO_USER', 'UserRole', user.id,
                             f'{user.username} <- {role.name}')
                db.session.commit()
                flash(f'Role "{role.name}" added to "{user.username}".', 'success')
            else:
                flash(f'User already has the role "{role.name}".', 'warning')
        else:
            flash('Role name cannot be empty.', 'danger')
        return redirect(url_for('super_admin.user_roles', user_id=user.id))

    all_roles = Role.query.order_by(Role.name).all()
    return render_template(
        'super_admin/user_roles.html',
        title='User Roles',
        user=user,
        all_roles=all_roles,
    )


@super_admin_bp.route('/roles', methods=['GET', 'POST'])
@login_required
@roles_required('SuperAdmin')
def roles():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        if not name:
            flash('Role name is required.', 'danger')
        else:
            role = Role.query.filter_by(name=name).first()
            if role:
                flash(f'Role "{name}" already exists.', 'warning')
            else:
                role = Role(name=name, description=description)
                db.session.add(role)
                db.session.commit()
                log_activity('CREATE_ROLE', 'Role', role.id, f'Role: {name}')
                db.session.commit()
                flash(f'Role "{name}" created successfully.', 'success')
        return redirect(url_for('super_admin.roles'))

    roles = Role.query.order_by(Role.name).all()
    return render_template('super_admin/roles.html', title='Manage Roles', roles=roles)


@super_admin_bp.route('/permissions', methods=['GET', 'POST'])
@login_required
@roles_required('SuperAdmin')
@permissions_required(ADMIN_MANAGE_PERMISSIONS)
def permissions():
    if request.method == 'POST':
        role_id = request.form.get('role_id', type=int)
        permission_name = request.form.get('permission_name', '').strip()
        resource = request.form.get('resource', '').strip()
        action = request.form.get('action', '').strip()

        role = db.session.get(Role, role_id) if role_id else None
        if role is None:
            flash('Please select a valid role.', 'danger')
        elif not permission_name:
            flash('Permission name is required.', 'danger')
        else:
            permission = Permission.query.filter_by(name=permission_name).first()
            if permission is None:
                permission = Permission(
                    name=permission_name,
                    resource=resource or None,
                    action=action or None,
                )
                db.session.add(permission)
                db.session.flush()
            if permission not in role.permissions:
                role.permissions.append(permission)
                db.session.commit()
                log_activity('ADD_PERMISSION_TO_ROLE', 'RolePermission', role.id,
                             f'{role.name} <- {permission.name}')
                db.session.commit()
                flash(f'Permission "{permission.name}" added to role "{role.name}".', 'success')
            else:
                flash('This permission is already assigned to the role.', 'warning')
        return redirect(url_for('super_admin.permissions'))

    roles = Role.query.order_by(Role.name).all()
    permissions = Permission.query.order_by(Permission.name).all()
    return render_template(
        'super_admin/permissions.html',
        title='Manage Permissions',
        roles=roles,
        permissions=permissions,
    )


@super_admin_bp.route('/audit-logs')
@login_required
@roles_required('SuperAdmin')
def audit_logs():
    logs = db.session.query(AuditLog).outerjoin(User, AuditLog.user_id == User.id).order_by(
        AuditLog.created_at.desc()).all()
    return render_template('super_admin/audit_logs.html', title='Audit Logs', logs=logs)


@super_admin_bp.route('/settings')
@login_required
@roles_required('SuperAdmin')
def settings():
    settings = SystemSetting.query.order_by(SystemSetting.category, SystemSetting.key).all()
    return render_template('super_admin/settings.html', title='System Settings', settings=settings)


@super_admin_bp.route('/backup', methods=['GET', 'POST'])
@login_required
@roles_required('SuperAdmin')
def backup():
    if request.method == 'POST':
        db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
        db_path = db_uri.replace('sqlite:///', '', 1)
        db_path = os.path.normpath(db_path)

        backups_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'backups'
        )
        os.makedirs(backups_dir, exist_ok=True)

        timestamp = utcnow().strftime('%Y%m%d_%H%M%S')
        base, ext = os.path.splitext(os.path.basename(db_path))
        dest_path = os.path.join(backups_dir, f'{base}_{timestamp}{ext}')

        shutil.copy2(db_path, dest_path)
        log_activity('BACKUP_DATABASE', 'Database', None, f'Backup -> {dest_path}')
        db.session.commit()
        flash(f'Database backup created successfully at {os.path.basename(dest_path)}.', 'success')
        return redirect(url_for('super_admin.backup'))

    db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    db_path = db_uri.replace('sqlite:///', '', 1)
    db_name = os.path.basename(db_path)
    backup_folder = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'backups'
    )
    return render_template(
        'super_admin/backup.html',
        title='Database Backup',
        db_name=db_name,
        backup_folder=backup_folder,
    )


@super_admin_bp.route('/preventive-sweep', methods=['POST'])
@login_required
@roles_required('SuperAdmin')
def preventive_sweep():
    """Manually run the preventive-care safety net (overdue/missed/due)."""
    from app.services.preventive import run_preventive_sweep
    result = run_preventive_sweep()
    log_activity('PREVENTIVE_SWEEP', 'system', None,
                 f'alerts/tasks created: {result}')
    total = sum(result.values())
    flash(f'Preventive-care sweep complete: {total} reminder(s) generated '
          f'({result.get("overdue_followup")} overdue follow-up, '
          f'{result.get("missed_appointment")} missed appointment, '
          f'{result.get("vaccine_due")} vaccine due, '
          f'{result.get("upcoming_followup_reminder")} upcoming follow-up).', 'success')
    return redirect(url_for('super_admin.dashboard'))
