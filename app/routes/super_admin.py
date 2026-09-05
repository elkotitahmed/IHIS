"""Super Admin portal - Executive command center with global visibility."""
import os
import shutil
from datetime import timedelta, datetime

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
    'RadiologyTechnician': 'Radiology Technician',
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
        LabOrder.status.in_(['Pending', 'Accepted', 'Collected', 'ReceivedAtLab',
                             'Processing', 'Reordered'])
    ).count()
    completed_lab_today = (LabOrder.query
                           .join(LabResult, LabResult.order_id == LabOrder.id)
                           .filter(LabResult.status.in_(['Verified', 'Locked', 'Finalized']),
                                   LabResult.result_date >= today_start)
                           .count())
    lab_volume_total = LabOrder.query.count()

    # --- Radiology Operations ---
    pending_radiology = RadiologyOrder.query.filter(
        RadiologyOrder.status.in_(['Pending', 'Scheduled', 'Arrived', 'InProgress', 'Performed'])
    ).count()
    completed_radiology_today = (RadiologyOrder.query
                                 .join(RadiologyReport, RadiologyReport.order_id == RadiologyOrder.id)
                                 .filter(RadiologyReport.status.in_(['Signed', 'Locked', 'Finalized']),
                                         RadiologyReport.report_date >= today_start)
                                 .count())
    radiology_volume_total = RadiologyOrder.query.count()

    # --- Prescription / Pharmacy ---
    pending_prescriptions = Prescription.query.filter(
        Prescription.status.in_(['Active', 'Pending'])
    ).count()
    dispensed_today = DispensingRecord.query.filter(
        DispensingRecord.dispensed_at >= today_start,
    ).count()
    pharmacy_items = PharmacyInventory.query.filter(
        PharmacyInventory.quantity > 0
    ).count()
    low_stock_items = PharmacyInventory.query.filter(
        PharmacyInventory.quantity <= PharmacyInventory.reorder_level
    ).count()

    # --- Nursing ---
    vitals_today = VitalSign.query.filter(
        VitalSign.recorded_at >= today_start,
    ).count()

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
    ).count()

    # --- Notifications ---
    unread_notifications_total = Notification.query.filter_by(is_read=False).count()

    # --- Alerts ---
    open_alerts = ClinicalAlert.query.filter(
        ClinicalAlert.status == 'OPEN'
    ).count()

    # --- Admissions & Discharges ---
    active_admissions = Admission.query.filter(
        Admission.status == 'Admitted'
    ).count()
    admissions_today = Admission.query.filter(
        Admission.admitted_at >= today_start
    ).count()
    discharges_today = Admission.query.filter(
        Admission.discharged_at >= today_start
    ).count()

    # --- Referrals ---
    pending_referrals = Referral.query.filter(
        Referral.status.in_(['Pending', 'SENT', 'ACCEPTED', 'IN_REVIEW'])
    ).count()

    # --- Dental ---
    dental_records = DentalRecord.query.count()

    # --- Physiotherapy ---
    therapy_sessions = TherapySession.query.filter(
        TherapySession.scheduled_at >= today_start,
        TherapySession.scheduled_at < today_start + timedelta(days=1),
    ).count()

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
        low_stock_items=low_stock_items,
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
    page = max(1, request.args.get('page', 1, type=int))
    action = (request.args.get('action') or '').strip()
    query = AuditLog.query
    if action:
        query = query.filter(AuditLog.action.ilike(f'%{action}%'))
    pagination = query.order_by(AuditLog.created_at.desc()).paginate(
        page=page, per_page=100, error_out=False)
    return render_template('super_admin/audit_logs.html', title='Audit Logs',
                           logs=pagination.items, pagination=pagination,
                           action_filter=action)


@super_admin_bp.route('/settings')
@login_required
@roles_required('SuperAdmin')
def settings():
    settings = SystemSetting.query.order_by(SystemSetting.category, SystemSetting.key).all()
    return render_template('super_admin/settings.html', title='System Settings', settings=settings)


def _backup_dir():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.environ.get('BACKUP_DIR') or os.path.join(root, 'backup', 'backups')


@super_admin_bp.route('/backup', methods=['GET', 'POST'])
@login_required
@roles_required('SuperAdmin')
def backup():
    """Create and list database backups (SQLite file copy or pg_dump) via
    the shared backup utility. Connection strings are never rendered."""
    import sys
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from pathlib import Path
    from backup import backup as backup_util

    db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    backend = 'PostgreSQL' if db_uri.startswith('postgresql') else (
        'SQLite' if db_uri.startswith('sqlite') else 'Database')
    backups_dir = Path(_backup_dir())

    if request.method == 'POST':
        backups_dir.mkdir(parents=True, exist_ok=True)
        try:
            if db_uri.startswith('postgresql'):
                dest = backup_util.backup_postgres(db_uri, backups_dir)
            elif db_uri.startswith('sqlite'):
                dest = backup_util.backup_sqlite(db_uri, backups_dir)
            else:
                raise RuntimeError('Unsupported database backend for in-app backup')
            ok = backup_util.verify_backup(Path(dest), db_uri)
        except SystemExit as exc:  # the utility exits on tool errors (pg_dump missing, ...)
            db.session.rollback()
            log_activity('BACKUP_DATABASE_FAILED', 'Database', None, f'exit={exc.code}')
            db.session.commit()
            flash('Backup failed: the backup tool reported an error (check that pg_dump is '
                  'installed and the database is reachable).', 'danger')
            return redirect(url_for('super_admin.backup'))
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            log_activity('BACKUP_DATABASE_FAILED', 'Database', None, type(exc).__name__)
            db.session.commit()
            flash('Backup failed: ' + type(exc).__name__, 'danger')
            return redirect(url_for('super_admin.backup'))
        log_activity('BACKUP_DATABASE', 'Database', None,
                     f'{os.path.basename(str(dest))} checksum_ok={ok}')
        db.session.commit()
        flash(f'Backup created: {os.path.basename(str(dest))}'
              + ('' if ok else ' (checksum verification FAILED)'),
              'success' if ok else 'danger')
        return redirect(url_for('super_admin.backup'))

    backups = []
    if backups_dir.is_dir():
        for f in sorted(backups_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
            if f.is_file() and not f.name.endswith('.sha256'):
                backups.append({'name': f.name, 'size_mb': round(f.stat().st_size / (1024 * 1024), 2),
                                'at': datetime.fromtimestamp(f.stat().st_mtime),
                                'checksum': (backups_dir / (f.name + '.sha256')).exists()})
    return render_template(
        'super_admin/backup.html',
        title='Database Backup',
        db_name=backend,
        backup_folder=str(backups_dir),
        backups=backups[:20],
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


# ---------------------------------------------------------------------------
# SYSTEM HEALTH — live checks, never fabricated
# ---------------------------------------------------------------------------
@super_admin_bp.route('/system-health')
@login_required
@roles_required('SuperAdmin')
def system_health():
    import platform
    import sys
    from sqlalchemy import text
    from datetime import datetime as _dt

    checks = []

    def add(name, ok, detail='', warn=False):
        checks.append({'name': name, 'ok': ok, 'warn': warn and not ok, 'detail': detail})

    # Database
    db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    backend = db_uri.split(':', 1)[0] if db_uri else 'unknown'
    try:
        db.session.execute(text('SELECT 1'))
        add('Database connection', True, f'{backend} reachable')
    except Exception as exc:  # noqa: BLE001
        add('Database connection', False, f'{type(exc).__name__}')

    # Migration head vs. applied version
    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cfg = AlembicConfig(os.path.join(root, 'migrations', 'alembic.ini'))
        cfg.set_main_option('script_location', os.path.join(root, 'migrations'))
        heads = ScriptDirectory.from_config(cfg).get_heads()
        applied = None
        try:
            applied = db.session.execute(text('SELECT version_num FROM alembic_version')).scalar()
        except Exception:  # noqa: BLE001
            db.session.rollback()
        if applied is None:
            add('Schema migrations', False,
                f'alembic_version table missing (script head {heads[0] if heads else "?"}); run flask db upgrade',
                warn=True)
        else:
            add('Schema migrations', len(heads) == 1 and applied == heads[0],
                f'applied {applied} · script head {heads[0] if heads else "?"}')
    except Exception as exc:  # noqa: BLE001
        add('Schema migrations', False, f'{type(exc).__name__}: {exc}', warn=True)

    # Storage
    upload = current_app.config.get('UPLOAD_FOLDER') or ''
    add('Private upload storage', bool(upload) and os.path.isdir(upload) and os.access(upload, os.W_OK),
        upload)
    static_private = os.path.join(current_app.static_folder or '', 'uploads')
    add('No PHI under public static', not os.path.isdir(static_private) or not os.listdir(static_private),
        'app/static/uploads is empty or absent', warn=True)

    # Security configuration
    prod = current_app.config.get('ENV_NAME') or ('production' if not current_app.debug else 'development')
    secret = current_app.config.get('SECRET_KEY', '')
    add('Strong SECRET_KEY', len(secret) >= 32 and 'change-me' not in secret,
        f'{len(secret)} characters', warn=current_app.debug)
    add('Session cookies secure', bool(current_app.config.get('SESSION_COOKIE_SECURE')),
        'SESSION_COOKIE_SECURE', warn=current_app.debug)
    add('CSRF protection', bool(current_app.config.get('WTF_CSRF_ENABLED', True)), 'Flask-WTF')
    add('Rate limiting', bool(current_app.config.get('RATELIMIT_ENABLED')),
        current_app.config.get('RATELIMIT_STORAGE_URI', 'memory://'),
        warn=True)
    add('Debug mode off', not current_app.debug, 'development profile' if current_app.debug else 'production profile',
        warn=True)

    # AI providers
    from app.services.ai import gemini_available
    add('Gemini API key configured', gemini_available(),
        'LLM features degrade gracefully when absent', warn=True)
    try:
        from app.services.ai.fracture_detection import fracture_model_available
        from app.services.ai.tooth_segmentation import tooth_model_available
        from app.services.ai.skin_lesion_classification import skin_model_available
        add('Fracture detection model', fracture_model_available(), 'YOLOv8 weights', warn=True)
        add('Tooth segmentation model', tooth_model_available(), 'U-Net weights', warn=True)
        add('Skin lesion model', skin_model_available(), 'ResNet/EfficientNet weights', warn=True)
    except Exception as exc:  # noqa: BLE001
        add('Image AI models', False, f'{type(exc).__name__}', warn=True)

    # Backups
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    backup_dir = os.environ.get('BACKUP_DIR') or os.path.join(root, 'backup', 'backups')
    latest = None
    if os.path.isdir(backup_dir):
        files = [f for f in os.listdir(backup_dir) if not f.endswith('.sha256')]
        if files:
            files.sort(key=lambda f: os.path.getmtime(os.path.join(backup_dir, f)), reverse=True)
            latest = files[0]
            latest_at = _dt.fromtimestamp(os.path.getmtime(os.path.join(backup_dir, latest)))
            add('Latest backup', True, f'{latest} · {latest_at:%Y-%m-%d %H:%M}')
    if latest is None:
        add('Latest backup', False, f'no backup found in {backup_dir}', warn=True)

    # Operational counters (real data)
    now = utcnow()
    seven = now - timedelta(days=7)
    stats = {
        'users': User.query.count(),
        'patients': Patient.query.count(),
        'failed_logins_7d': AuditLog.query.filter(AuditLog.action.in_(DASHBOARD_ACTIONS),
                                                  AuditLog.created_at >= seven).count(),
        'open_tasks': Task.query.filter(Task.status.in_(['NEW', 'ASSIGNED', 'IN_PROGRESS'])).count(),
        'overdue_tasks': Task.query.filter(Task.status.in_(['NEW', 'ASSIGNED', 'IN_PROGRESS']),
                                           Task.due_at < now).count(),
        'open_alerts': ClinicalAlert.query.filter_by(status='OPEN').count(),
        'critical_alerts': ClinicalAlert.query.filter_by(status='OPEN', severity='CRITICAL').count(),
        'unread_notifications': Notification.query.filter_by(is_read=False).count(),
        'audit_24h': AuditLog.query.filter(AuditLog.created_at >= now - timedelta(days=1)).count(),
        'ai_calls_7d': AuditLog.query.filter(AuditLog.action.like('AI_%'),
                                             AuditLog.created_at >= seven).count(),
    }
    env = {
        'python': sys.version.split()[0],
        'platform': platform.platform(),
        'db_backend': backend,
        'config': 'debug' if current_app.debug else 'production',
    }
    failing = [c for c in checks if not c['ok'] and not c['warn']]
    warnings = [c for c in checks if not c['ok'] and c['warn']]
    overall = 'Operational' if not failing else 'Degraded'
    return render_template('super_admin/system_health.html', title='System Health',
                           checks=checks, stats=stats, env=env, overall=overall,
                           failing=len(failing), warnings=len(warnings), now=now)


# ---------------------------------------------------------------------------
# HOSPITAL DEMO CENTER — scenario walkthroughs backed by REAL records
# ---------------------------------------------------------------------------
@super_admin_bp.route('/demo')
@login_required
@roles_required('SuperAdmin')
def demo():
    """Each scenario resolves the most recent real record chain in the
    database and links every step to the live page that shows it. Nothing
    is fabricated: a step whose record does not exist yet says so."""
    from app.models import (MedicalRecord, DispensingRecord, MedicationAdministration,
                            TherapySession, DentalProcedure, PatientDocument)

    def _pat(rec):
        return rec.patient if rec is not None and getattr(rec, 'patient', None) else None

    latest_completed_appt = Appointment.query.filter_by(status='Completed').order_by(
        Appointment.scheduled_at.desc()).first()
    latest_record = MedicalRecord.query.order_by(MedicalRecord.visit_date.desc()).first()
    verified_lab = (LabOrder.query.join(LabResult, LabResult.order_id == LabOrder.id)
                    .filter(LabResult.status.in_(['Verified', 'Locked', 'Finalized']))
                    .order_by(LabOrder.order_date.desc()).first())
    critical_lab = (LabOrder.query.join(LabResult, LabResult.order_id == LabOrder.id)
                    .filter(LabResult.is_critical.is_(True)).order_by(LabOrder.order_date.desc()).first())
    signed_rad = (RadiologyOrder.query.join(RadiologyReport, RadiologyReport.order_id == RadiologyOrder.id)
                  .filter(RadiologyReport.status.in_(['Signed', 'Locked', 'Finalized']))
                  .order_by(RadiologyOrder.order_date.desc()).first())
    dispensed = DispensingRecord.query.order_by(DispensingRecord.dispensed_at.desc()).first()
    rx_pending = Prescription.query.filter_by(status='Active').order_by(Prescription.prescribed_date.desc()).first()
    mar = MedicationAdministration.query.order_by(MedicationAdministration.created_at.desc()).first()
    therapy = TherapySession.query.order_by(TherapySession.scheduled_at.desc()).first()
    dental = DentalProcedure.query.order_by(DentalProcedure.performed_at.desc()).first()
    referral = Referral.query.order_by(Referral.created_at.desc()).first()
    paid_bill = Bill.query.filter_by(status='Paid').order_by(Bill.issued_at.desc()).first()
    open_bill = Bill.query.filter(Bill.status.in_(['Unpaid', 'PartiallyPaid'])).order_by(Bill.issued_at.desc()).first()
    admission = Admission.query.order_by(Admission.admitted_at.desc()).first()
    portal_patient = Patient.query.filter(Patient.user_id.isnot(None)).order_by(Patient.id.asc()).first()
    document = PatientDocument.query.order_by(PatientDocument.uploaded_at.desc()).first()

    def step(label, url, role, ok=True, note=None):
        return {'label': label, 'url': url, 'role': role, 'ok': ok, 'note': note}

    def p360(p):
        return url_for('clinical.patient_360', patient_id=p.id) if p else None

    scenarios = []

    p = _pat(latest_completed_appt) or _pat(latest_record)
    scenarios.append({
        'key': 'outpatient', 'icon': 'fa-user-md',
        'title': 'Outpatient Visit', 'title_ar': 'زيارة عيادة خارجية',
        'summary': 'Registration → appointment → check-in → consultation → encounter note → consultation bill.',
        'patient': p,
        'steps': [
            step('Register / find patient', url_for('reception.register'), 'Receptionist'),
            step('Book appointment', url_for('reception.book_appointment'), 'Receptionist'),
            step('Check-in queue', url_for('reception.queue'), 'Receptionist'),
            step('Doctor appointments & start consultation', url_for('doctor.appointments'), 'Doctor'),
            step('Encounter note (medical record)', url_for('doctor.patient_detail', patient_id=p.id) if p else url_for('doctor.patients'),
                 'Doctor', ok=bool(latest_record), note=None if latest_record else 'No encounter recorded yet'),
            step('Consultation bill', url_for('billing.bills', status='Unpaid'), 'Cashier'),
            step('Patient 360', p360(p) or url_for('clinical.workbench'), 'Any clinician', ok=bool(p)),
        ]})

    p = _pat(verified_lab) or _pat(critical_lab)
    scenarios.append({
        'key': 'lab', 'icon': 'fa-flask',
        'title': 'Doctor → Laboratory', 'title_ar': 'الطبيب ← المختبر',
        'summary': 'Order → accept → collect → receive → process → result → verify (critical escalation) → doctor inbox → PDF.',
        'patient': p,
        'steps': [
            step('Order a test', url_for('doctor.lab_order', patient_id=p.id) if p else url_for('lab.new_order'), 'Doctor'),
            step('Lab work queue', url_for('lab.orders'), 'Lab Technician'),
            step('Result entry & verification', url_for('lab.enter_result', order_id=verified_lab.id) if verified_lab else url_for('lab.orders'),
                 'Lab Technician', ok=bool(verified_lab), note=None if verified_lab else 'No verified result yet'),
            step('Critical value escalation', url_for('lab.enter_result', order_id=critical_lab.id) if critical_lab else url_for('lab.orders', critical=1),
                 'Lab Technician → Doctor', ok=bool(critical_lab), note=None if critical_lab else 'No critical result yet'),
            step('Doctor results inbox', url_for('clinical.inbox'), 'Doctor'),
            step('Lab report PDF', url_for('reports.lab_result', order_id=verified_lab.id) if verified_lab else url_for('reports.dashboard'),
                 'Doctor / Patient', ok=bool(verified_lab)),
        ]})

    p = _pat(signed_rad)
    scenarios.append({
        'key': 'radiology', 'icon': 'fa-x-ray',
        'title': 'Doctor → Radiology', 'title_ar': 'الطبيب ← الأشعة',
        'summary': 'Order → safety screening → schedule → arrive → perform (technician) → report → sign (radiologist) → notify → PDF.',
        'patient': p,
        'steps': [
            step('Order imaging', url_for('doctor.radiology_order', patient_id=p.id) if p else url_for('radiology.new_order'), 'Doctor'),
            step('Study worklist (technician)', url_for('radiology.orders'), 'Radiology Technician'),
            step('Safety screening', url_for('radiology.safety_screening', order_id=signed_rad.id) if signed_rad else url_for('radiology.orders'),
                 'Technician / Nurse', ok=bool(signed_rad)),
            step('Report & sign', url_for('radiology.enter_report', order_id=signed_rad.id) if signed_rad else url_for('radiology.orders'),
                 'Radiologist', ok=bool(signed_rad), note=None if signed_rad else 'No signed report yet'),
            step('Critical findings board', url_for('radiology.critical_findings'), 'Radiologist / Doctor'),
            step('Radiology report PDF', url_for('reports.radiology_report', order_id=signed_rad.id) if signed_rad else url_for('reports.dashboard'),
                 'Doctor / Patient', ok=bool(signed_rad)),
        ]})

    rx = dispensed.prescription if dispensed else rx_pending
    p = _pat(rx)
    scenarios.append({
        'key': 'pharmacy', 'icon': 'fa-pills',
        'title': 'Doctor → Pharmacy', 'title_ar': 'الطبيب ← الصيدلية',
        'summary': 'Prescription → safety alerts → pharmacy queue → review / intervention → FEFO dispensing → stock ledger → bill.',
        'patient': p,
        'steps': [
            step('Write prescription', url_for('doctor.prescriptions', patient_id=p.id) if p else url_for('doctor.patients'), 'Doctor'),
            step('Pharmacy queue', url_for('pharmacy.prescriptions'), 'Pharmacist'),
            step('Prescription review & dispense', url_for('pharmacy.prescription_detail', rx_id=rx.id) if rx else url_for('pharmacy.prescriptions'),
                 'Pharmacist', ok=bool(rx), note=None if rx else 'No prescription yet'),
            step('AI medication review', url_for('ai.medication_review', patient_id=p.id) if p else url_for('pharmacy.ai_workbench'), 'Pharmacist'),
            step('Stock ledger', url_for('pharmacy.transactions'), 'Pharmacist'),
            step('Interventions to prescriber', url_for('pharmacy.interventions'), 'Pharmacist ↔ Doctor'),
        ]})

    p = _pat(mar) or _pat(admission)
    scenarios.append({
        'key': 'nursing', 'icon': 'fa-user-nurse',
        'title': 'Nursing Medication (MAR)', 'title_ar': 'إعطاء الأدوية (التمريض)',
        'summary': 'Admission → vitals → nursing note → care plan → MAR schedule → Given / Held / Refused / Missed / Discontinued.',
        'patient': p,
        'steps': [
            step('Nurse workspace', url_for('nursing.dashboard'), 'Nurse'),
            step('Record vitals', url_for('nursing.vitals', patient_id=p.id) if p else url_for('nursing.patients'), 'Nurse', ok=bool(p)),
            step('MAR for the patient', url_for('nursing.mar', patient_id=p.id) if p else url_for('nursing.medication_schedule'),
                 'Nurse', ok=bool(mar), note=None if mar else 'No dose scheduled yet'),
            step('Medication schedule board', url_for('nursing.medication_schedule'), 'Nurse'),
            step('Admission detail', url_for('admissions.view', id=admission.id) if admission else url_for('admissions.dashboard'),
                 'Nurse / Doctor', ok=bool(admission)),
        ]})

    p = _pat(therapy)
    scenarios.append({
        'key': 'physio', 'icon': 'fa-person-walking',
        'title': 'Physiotherapy', 'title_ar': 'العلاج الطبيعي',
        'summary': 'Referral → assessment → treatment plan → sessions (start / complete / no-show) → progress → session bill.',
        'patient': p,
        'steps': [
            step('Rehab dashboard (incoming referrals)', url_for('physiotherapy.dashboard'), 'Physiotherapist'),
            step('Assessment', url_for('physiotherapy.assessment', patient_id=p.id) if p else url_for('physiotherapy.patients'), 'Physiotherapist', ok=bool(p)),
            step('Treatment plan & sessions', url_for('physiotherapy.session', plan_id=therapy.plan_id) if therapy and therapy.plan_id else url_for('physiotherapy.patients'),
                 'Physiotherapist', ok=bool(therapy), note=None if therapy else 'No session yet'),
            step('Progress tracking', url_for('physiotherapy.progress', patient_id=p.id) if p else url_for('physiotherapy.patients'), 'Physiotherapist', ok=bool(p)),
            step('AI rehab insights', url_for('ai.rehab', patient_id=p.id) if p else url_for('physiotherapy.patients'), 'Physiotherapist', ok=bool(p)),
        ]})

    p = _pat(dental)
    scenarios.append({
        'key': 'dentistry', 'icon': 'fa-tooth',
        'title': 'Dentistry', 'title_ar': 'طب الأسنان',
        'summary': 'Intake record → odontogram (tooth-level history) → treatment plan → procedures → completion bill.',
        'patient': p,
        'steps': [
            step('Dental dashboard', url_for('dentistry.dashboard'), 'Dentist'),
            step('Dental intake record', url_for('dentistry.record', patient_id=p.id) if p else url_for('dentistry.patients'), 'Dentist', ok=bool(p)),
            step('Odontogram', url_for('dentistry.chart', patient_id=p.id) if p else url_for('dentistry.patients'), 'Dentist', ok=bool(p)),
            step('Treatment plans & procedures', url_for('dentistry.treatment_plans', patient_id=p.id) if p else url_for('dentistry.patients'),
                 'Dentist', ok=bool(dental), note=None if dental else 'No procedure yet'),
            step('AI tooth segmentation', url_for('ai.tooth_segmentation'), 'Dentist'),
        ]})

    p = _pat(referral)
    scenarios.append({
        'key': 'referral', 'icon': 'fa-share-nodes',
        'title': 'Referral', 'title_ar': 'الإحالة',
        'summary': 'Doctor refers → task + notification to the receiving service → accept / review / complete → referrer notified.',
        'patient': p,
        'steps': [
            step('Referral worklist', url_for('care.referrals'), 'Doctor / Specialist'),
            step('Care team', url_for('care.team', patient_id=p.id) if p else url_for('clinical.workbench'), 'Doctor', ok=bool(p)),
            step('Task engine', url_for('tasks.queue', department='Care Coordination'), 'All staff'),
            step('Patient timeline', p360(p) or url_for('clinical.workbench'), 'Any clinician', ok=bool(referral),
                 note=None if referral else 'No referral yet'),
        ]})

    bill = open_bill or paid_bill
    p = _pat(bill)
    scenarios.append({
        'key': 'billing', 'icon': 'fa-file-invoice-dollar',
        'title': 'Billing & Payment', 'title_ar': 'الفوترة والدفع',
        'summary': 'Auto-generated bills (consultation / lab / imaging / pharmacy / room) → cashier payment with receipt → idempotent references → reports.',
        'patient': p,
        'steps': [
            step('Cashier desk', url_for('billing.dashboard'), 'Cashier'),
            step('Open invoice & record payment', url_for('billing.view_bill', bill_id=bill.id) if bill else url_for('billing.bills'),
                 'Cashier', ok=bool(bill), note=None if bill else 'No bill yet'),
            step('Paid receipt example', url_for('billing.view_bill', bill_id=paid_bill.id) if paid_bill else url_for('billing.bills', status='Paid'),
                 'Cashier', ok=bool(paid_bill)),
            step('Revenue report', url_for('billing.reports'), 'Cashier / Admin'),
            step('Service catalog', url_for('billing.service_catalog'), 'Admin'),
        ]})

    p = portal_patient
    scenarios.append({
        'key': 'portal', 'icon': 'fa-mobile-screen',
        'title': 'Patient Portal', 'title_ar': 'بوابة المريض',
        'summary': 'Patient books, sees results/prescriptions/bills, uploads documents, messages the doctor, reads AI health insights.',
        'patient': p,
        'steps': [
            step('Preview the portal as this patient', url_for('patient.preview_as', patient_id=p.id) if p else url_for('patient.dashboard'),
                 'SuperAdmin (read-only preview)', ok=bool(p)),
            step('Lab results', url_for('patient.lab_results'), 'Patient'),
            step('Documents (upload)', url_for('patient.documents'), 'Patient', ok=bool(document),
                 note=None if document else 'No document uploaded yet'),
            step('Bills & receipts', url_for('patient.bills'), 'Patient'),
            step('AI health insights', url_for('ai.health_insights', pid=p.id) if p else url_for('ai.health_insights'), 'Patient'),
            step('Role preview: Patient', url_for('super_admin.preview_role', role_name='Patient'), 'SuperAdmin'),
        ]})

    total_steps = sum(len(sc['steps']) for sc in scenarios)
    ready_steps = sum(1 for sc in scenarios for st in sc['steps'] if st['ok'])
    return render_template('super_admin/demo.html', title='Hospital Demo Center',
                           scenarios=scenarios, total_steps=total_steps,
                           ready_steps=ready_steps,
                           patient_count=Patient.query.count())
