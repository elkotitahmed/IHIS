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
    """Catalogue of what the platform does, grouped by area. Every entry links
    to a live page (validated by tests/test_health_insights.py)."""
    def cap(label, label_ar, desc, desc_ar, roles, url, icon, model=False):
        return {'label': label, 'label_ar': label_ar, 'description': desc, 'description_ar': desc_ar,
                'roles': [r.strip() for r in roles.split(',')], 'url': url, 'icon': icon, 'model': model}

    groups = [
        {'key': 'ai', 'ai': True, 'tone': 'ai', 'icon': 'fa-wand-magic-sparkles',
         'label': 'AI models & engines', 'label_ar': 'نماذج الذكاء الاصطناعي ومحركاته',
         'desc': 'Four local imaging models, a copilot on every page, and rule engines that stay authoritative.',
         'desc_ar': 'أربعة نماذج صور محلية، مساعد في كل صفحة، ومحركات قواعد تبقى هي المرجع.',
         'capabilities': [
             cap('Chest X-ray Screening', 'فحص أشعة الصدر', '18 findings on a frontal chest X-ray (DenseNet-121, local); confident critical findings raise an alert.',
                 '18 نتيجة في أشعة الصدر الأمامية (DenseNet-121 محلي)؛ النتائج الحرجة الواثقة تُنشئ تنبيهًا.', 'Radiologist, Physician', '/ai/chest-xray', 'fa-lungs', True),
             cap('Fracture Detection', 'كشف الكسور', 'YOLOv8 fracture detection on bone X-rays with an annotated image.',
                 'كشف الكسور في أشعة العظام بـ YOLOv8 مع صورة موضّحة.', 'Radiologist, Physician, Nurse', '/ai/fracture-detection', 'fa-bone', True),
             cap('Tooth Segmentation', 'تجزئة الأسنان', 'U-Net segmentation mask on panoramic dental X-rays.',
                 'قناع تجزئة U-Net على الأشعة البانورامية للأسنان.', 'Dentist, Radiologist', '/ai/tooth-segmentation', 'fa-teeth', True),
             cap('Skin Lesion Detection', 'كشف آفات الجلد', 'ResNet-50 + EfficientNet-B0 ensemble: melanoma vs nevus with a Grad-CAM heatmap; confident melanoma calls alert the dermatologist.',
                 'مجموعة ResNet-50 + EfficientNet-B0: ميلانوما أم وحمة مع خريطة Grad-CAM؛ النداءات الواثقة تنبّه طبيب الجلدية.', 'Physician (Dermatology), Dentist, Nurse', '/ai/skin-lesion-detection', 'fa-person-circle-question', True),
             cap('AI Clinical Copilot', 'المساعد السريري الذكي', 'One entry point on every clinical page: summary, differential support, documentation drafts, safety review, patient communication. Reads the chart, never writes to it.',
                 'نقطة دخول واحدة في كل صفحة سريرية: ملخص، دعم تفريقي، مسودات توثيق، مراجعة سلامة، تواصل مع المريض. يقرأ الملف ولا يكتب فيه.', 'Physician, Nurse, Pharmacist, Dentist', '/ai/hub', 'fa-wand-magic-sparkles'),
             cap('Radiology critical-finding engine', 'محرك النتائج الحرجة للأشعة', 'Rules + local negation classifier on every signed report → alert → urgent task → acknowledgement → audit.',
                 'قواعد + مصنّف نفي محلي على كل تقرير موقّع ← تنبيه ← مهمة عاجلة ← إقرار ← تدقيق.', 'Radiologist, Physician', '/ai/copilot/radiology', 'fa-x-ray'),
             cap('Clinical pharmacist rules engine', 'محرك الصيدلي الإكلينيكي', 'Cockcroft-Gault renal dosing, interactions against all active medications (formulary + DDInter), contraindicated combinations, drug-lab conflicts; prefilled interventions.',
                 'جرعات كلوية بمعادلة Cockcroft-Gault، تداخلات ضد كل الأدوية النشطة (القائمة + DDInter)، تركيبات مضادة للاستطباب، تعارضات دواء/تحليل؛ تدخلات مُعبّأة مسبقًا.', 'Pharmacist, Physician', '/pharmacy/ai-workbench', 'fa-user-doctor'),
             cap('Specialty-aware model access', 'النماذج حسب التخصص', 'Each physician sees only the models of their specialty (dermatology → skin, orthopaedics → fracture…); enforced on the routes.',
                 'كل طبيب يرى نماذج تخصصه فقط (جلدية ← الجلد، عظام ← الكسور…) مع تطبيق ذلك على المسارات.', 'All clinical roles', '/ai/hub', 'fa-user-shield'),
             cap('Dictation (local Whisper)', 'الإملاء الصوتي المحلي', 'Microphone next to every clinical note; transcribed on the server with faster-whisper.',
                 'ميكروفون بجانب كل ملاحظة سريرية؛ يُفرَّغ على الخادم بـ faster-whisper.', 'Physician, Nurse, Dentist', '/ai/hub', 'fa-microphone'),
             cap('Health Insights', 'الرؤى الصحية', 'Explainable risk profile per patient (age, comorbidity, kidney function, NEWS2, labs, medication safety, alerts); AI narrative on request.',
                 'ملف مخاطر قابل للتفسير لكل مريض (عمر، أمراض مصاحبة، كلى، NEWS2، تحاليل، سلامة الأدوية، تنبيهات)؛ سرد الذكاء عند الطلب.', 'Patient, Admin', '/ai/health-insights', 'fa-brain'),
             cap('Capacity & no-show model', 'السعة ونموذج التغيّب', 'Clinic utilisation for the next 7 days and a locally trained no-show model.',
                 'إشغال العيادات للأيام السبعة القادمة ونموذج تغيّب مدرَّب محليًا.', 'Admin', '/admin/capacity', 'fa-chart-line'),
             cap('AI Control Center', 'مركز التحكم بالذكاء', 'Budget, usage, cache, failures, latency, feature/role usage and audit trail.',
                 'الميزانية، الاستخدام، الذاكرة المؤقتة، الأعطال، زمن الاستجابة، استخدام الأدوار وسجل التدقيق.', 'SuperAdmin', '/super-admin/ai-control', 'fa-sliders'),
         ]},
        {'key': 'clinical', 'ai': False, 'tone': 'primary', 'icon': 'fa-stethoscope',
         'label': 'Clinical', 'label_ar': 'السريري', 'desc': 'The chart and everything around it.', 'desc_ar': 'الملف الطبي وكل ما حوله.',
         'capabilities': [
             cap('Patients', 'المرضى', 'Search, MRN, care setting (outpatient / inpatient).', 'بحث، رقم طبي، نوع الرعاية (عيادة / تنويم).', 'Physician, Admin', '/doctor/patients', 'fa-user-injured'),
             cap('Patient 360', 'ملف المريض 360', 'Timeline, allergies, problems, alerts, orders and results in one view.', 'الخط الزمني والحساسية والمشاكل والتنبيهات والأوامر والنتائج في شاشة واحدة.', 'All clinical staff', '/clinical', 'fa-circle-user'),
             cap('Encounters & EMR', 'اللقاءات والسجل', 'Structured notes, diagnoses (ICD-10-CM), orders, prescriptions, signing and amendments.', 'ملاحظات مهيكلة، تشخيصات ICD-10-CM، أوامر، وصفات، توقيع وتعديل.', 'Physician', '/doctor/patients', 'fa-file-medical'),
             cap('Clinical alerts', 'التنبيهات السريرية', 'Severity lifecycle: open → acknowledged → in progress → resolved, with escalation.', 'دورة حياة بحسب الخطورة: مفتوح ← مُقرّ ← قيد المعالجة ← محلول، مع تصعيد.', 'All clinical staff', '/clinical/alerts', 'fa-bell'),
             cap('Clinical inbox', 'الصندوق السريري', 'Unreviewed results and messages, prioritised by rules.', 'نتائج ورسائل غير مراجعة مرتبة بالقواعد.', 'Physician, Nurse', '/clinical/inbox', 'fa-inbox'),
             cap('Early-warning scores', 'درجات الإنذار المبكر', 'NEWS2, qSOFA and LACE computed from vitals and admissions.', 'NEWS2 وqSOFA وLACE محسوبة من العلامات الحيوية والتنويم.', 'Nurse, Physician', '/nursing/dashboard', 'fa-heart-pulse'),
             cap('Referrals & MDT cases', 'الإحالات والحالات متعددة التخصصات', 'Cross-department referrals and multidisciplinary case boards.', 'إحالات بين الأقسام ولوحات حالات متعددة التخصصات.', 'Physician', '/care/referrals', 'fa-share-nodes'),
             cap('Order sets & templates', 'حزم الأوامر والقوالب', 'Clinician-authored order bundles and structured note templates.', 'حزم أوامر يؤلفها الأطباء وقوالب ملاحظات مهيكلة.', 'Physician', '/clinical/order-sets', 'fa-layer-group'),
         ]},
        {'key': 'diagnostics', 'ai': False, 'tone': 'info', 'icon': 'fa-flask',
         'label': 'Diagnostics', 'label_ar': 'التشخيص', 'desc': 'Laboratory and imaging workflows.', 'desc_ar': 'مسارات المختبر والأشعة.',
         'capabilities': [
             cap('Laboratory work queue', 'قائمة عمل المختبر', 'Order → specimen → result → verification, with critical-value escalation.', 'طلب ← عينة ← نتيجة ← توثيق، مع تصعيد القيم الحرجة.', 'Lab Technician, Physician', '/lab/orders', 'fa-vials'),
             cap('Test catalogue', 'دليل الفحوصات', 'Reference ranges, units and critical thresholds.', 'المعدلات المرجعية والوحدات والعتبات الحرجة.', 'Lab Technician, Admin', '/lab/catalog', 'fa-book'),
             cap('Radiology worklist & reporting', 'قائمة الأشعة والتقارير', 'Order, schedule, perform, report and sign; report versions kept.', 'طلب، جدولة، تنفيذ، تقرير وتوقيع؛ مع حفظ نسخ التقرير.', 'Radiologist, Technologist', '/radiology/orders', 'fa-x-ray'),
             cap('Imaging safety & dose', 'سلامة التصوير والجرعة', 'MRI implant registry, contrast history, dose records against reference levels.', 'سجل الزرعات للرنين، تاريخ الصبغة، سجلات الجرعة مقابل المستويات المرجعية.', 'Radiologist, Technologist', '/radiology/dose-dashboard', 'fa-radiation'),
         ]},
        {'key': 'medication', 'ai': False, 'tone': 'success', 'icon': 'fa-pills',
         'label': 'Medication', 'label_ar': 'الأدوية', 'desc': 'From prescription to administration.', 'desc_ar': 'من الوصفة إلى الإعطاء.',
         'capabilities': [
             cap('Prescription queue & dispensing', 'طابور الوصفات والصرف', 'Verification, FEFO batch dispensing, partial dispensing.', 'التحقق، صرف الدفعات بأسلوب FEFO، الصرف الجزئي.', 'Pharmacist', '/pharmacy/prescriptions', 'fa-prescription'),
             cap('Inventory', 'المخزون', 'Batches, expiry alerts, low stock, adjustments.', 'دفعات، تنبيهات انتهاء، نقص مخزون، تعديلات.', 'Pharmacist, Admin', '/pharmacy/inventory', 'fa-boxes-stacked'),
             cap('Interaction check', 'فحص التفاعلات', 'Formulary pairs plus the DDInter reference (160k pairs).', 'أزواج القائمة المحلية مع مرجع DDInter (160 ألف زوج).', 'Pharmacist, Physician, Nurse', '/pharmacy/drug-check', 'fa-dna'),
             cap('Official drug reference', 'المرجع الدوائي الرسمي', 'openFDA label sections and RxNorm identifiers per medication.', 'أقسام نشرة openFDA ومعرّفات RxNorm لكل دواء.', 'Pharmacist', '/pharmacy/medications', 'fa-book-medical'),
             cap('Medication reconciliation', 'مطابقة الأدوية', 'Home list vs active orders at admission, transfer and discharge.', 'قائمة المنزل مقابل الأوامر النشطة عند الدخول والنقل والخروج.', 'Pharmacist', '/pharmacy/reconciliations', 'fa-list-check'),
             cap('Pharmacist interventions', 'تدخلات الصيدلي', 'Auditable prescriber loop: issue, recommendation, response.', 'حلقة قابلة للتدقيق مع الطبيب: المشكلة، التوصية، الرد.', 'Pharmacist, Physician', '/pharmacy/interventions', 'fa-flag'),
             cap('Medication administration (MAR)', 'إعطاء الأدوية', 'Due doses, administration record and outcomes.', 'الجرعات المستحقة وسجل الإعطاء والنتائج.', 'Nurse', '/nursing/medication-schedule', 'fa-syringe'),
         ]},
        {'key': 'nursing', 'ai': False, 'tone': 'purple', 'icon': 'fa-user-nurse',
         'label': 'Nursing & rehabilitation', 'label_ar': 'التمريض والتأهيل', 'desc': 'Bedside documentation and therapy.', 'desc_ar': 'التوثيق بجانب السرير والعلاج.',
         'capabilities': [
             cap('Vital signs & NEWS2', 'العلامات الحيوية وNEWS2', 'Vitals entry with automatic NEWS2 / qSOFA alerts.', 'إدخال العلامات الحيوية مع تنبيهات NEWS2 / qSOFA تلقائية.', 'Nurse', '/nursing/patients', 'fa-heart-pulse'),
             cap('Nursing notes, care plans, intake/output', 'ملاحظات التمريض وخطط الرعاية والسوائل', 'Per-patient nursing documentation and fluid balance.', 'توثيق تمريضي لكل مريض وميزان السوائل.', 'Nurse', '/nursing/patients', 'fa-clipboard-list'),
             cap('Physiotherapy', 'العلاج الطبيعي', 'Assessments, plans, sessions, exercise library, outcomes.', 'تقييمات، خطط، جلسات، مكتبة تمارين، نتائج.', 'Physiotherapist', '/physiotherapy/patients', 'fa-person-walking'),
             cap('Dentistry', 'طب الأسنان', 'Odontogram, treatment plans, procedures, imaging and orthodontic cases.', 'مخطط الأسنان، خطط العلاج، الإجراءات، التصوير وحالات التقويم.', 'Dentist', '/dentistry/patients', 'fa-tooth'),
         ]},
        {'key': 'operations', 'ai': False, 'tone': 'warning', 'icon': 'fa-building',
         'label': 'Operations & finance', 'label_ar': 'التشغيل والمالية', 'desc': 'Front desk, beds, tasks and billing.', 'desc_ar': 'الاستقبال والأسرّة والمهام والفوترة.',
         'capabilities': [
             cap('Appointments & queue', 'المواعيد والطابور', 'Scheduling, walk-ins, check-in queue.', 'الجدولة، الحالات الطارئة، طابور التسجيل.', 'Receptionist, Physician', '/reception/appointments', 'fa-calendar-check'),
             cap('Admissions & beds', 'التنويم والأسرّة', 'Ward/bed board, admission, transfer, discharge summary.', 'لوحة الأجنحة والأسرّة، الدخول، النقل، ملخص الخروج.', 'Receptionist, Nurse, Physician', '/admissions/dashboard', 'fa-bed-pulse'),
             cap('Task engine', 'محرك المهام', 'Cross-department queue with priorities and escalation.', 'قائمة عمل بين الأقسام بأولويات وتصعيد.', 'All staff', '/tasks/my-tasks', 'fa-list-check'),
             cap('Billing & payments', 'الفوترة والمدفوعات', 'Bills from clinical events, payments, receipts, service catalogue.', 'فواتير من الأحداث السريرية، مدفوعات، إيصالات، دليل الخدمات.', 'Cashier, Admin', '/billing/dashboard', 'fa-file-invoice-dollar'),
             cap('Reports', 'التقارير', 'Operational and clinical reports, printable records.', 'تقارير تشغيلية وسريرية وسجلات قابلة للطباعة.', 'Admin', '/reports/', 'fa-chart-simple'),
         ]},
        {'key': 'admin', 'ai': False, 'tone': 'muted', 'icon': 'fa-cog',
         'label': 'Administration & governance', 'label_ar': 'الإدارة والحوكمة', 'desc': 'Users, roles, audit and system health.', 'desc_ar': 'المستخدمون والأدوار والتدقيق وصحة النظام.',
         'capabilities': [
             cap('Users & staff', 'المستخدمون والموظفون', 'Create, activate, deactivate; physician specialties drive model access.', 'إنشاء وتفعيل وتعطيل؛ تخصص الطبيب يحدد نماذجه.', 'Admin, SuperAdmin', '/admin/staff', 'fa-users'),
             cap('Roles & permissions', 'الأدوار والصلاحيات', 'Fine-grained permissions per role; need-to-know patient access.', 'صلاحيات دقيقة لكل دور؛ وصول للمرضى بحسب الحاجة.', 'SuperAdmin', '/super-admin/roles', 'fa-user-shield'),
             cap('Audit logs', 'سجلات التدقيق', 'Every action, AI run and review is logged without patient prompts.', 'كل إجراء وتشغيل ومراجعة للذكاء مسجّل بلا نصوص مرضى.', 'SuperAdmin', '/super-admin/audit-logs', 'fa-clipboard-check'),
             cap('System health & backup', 'صحة النظام والنسخ الاحتياطي', 'Database, migrations, models installed, backups.', 'قاعدة البيانات، الترحيلات، النماذج المثبتة، النسخ الاحتياطية.', 'SuperAdmin', '/super-admin/system-health', 'fa-heart-pulse'),
             cap('Patient portal', 'بوابة المريض', 'Own record, appointments, results in plain language, safe patient AI.', 'السجل الخاص، المواعيد، النتائج بلغة بسيطة، ذكاء آمن للمريض.', 'Patient', '/patient/dashboard', 'fa-mobile-screen'),
         ]},
    ]
    total = sum(len(g['capabilities']) for g in groups)
    ai_group = groups[0]
    stats = [
        {'label': 'AI models (local)', 'label_ar': 'نماذج محلية', 'count': sum(1 for c in ai_group['capabilities'] if c['model']), 'icon': 'fa-microchip', 'tone': 'primary'},
        {'label': 'AI engines & tools', 'label_ar': 'محركات وأدوات الذكاء', 'count': len(ai_group['capabilities']), 'icon': 'fa-wand-magic-sparkles', 'tone': 'info'},
        {'label': 'Clinical areas', 'label_ar': 'مجالات سريرية', 'count': len(groups) - 1, 'icon': 'fa-hospital', 'tone': 'success'},
        {'label': 'Capabilities', 'label_ar': 'القدرات', 'count': total, 'icon': 'fa-rocket', 'tone': 'warning'},
    ]
    return render_template('super_admin/capabilities.html', title='Platform Capabilities', groups=groups,
                           stats=stats, total=total)


# ---------------------------------------------------------------------------
# ROLE PREVIEW — Simulate the UI as another role
# ---------------------------------------------------------------------------
@super_admin_bp.route('/preview/<role_name>')
@login_required
@roles_required('SuperAdmin')
def preview_role(role_name):
    """Set a session flag so the base template renders the specified role's UI."""
    # Display terminology: the UI offers "Physician"; the role is stored as "Doctor".
    role_name = {'Physician': 'Doctor', 'physician': 'Doctor'}.get(role_name, role_name)
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
# ---------------------------------------------------------------------------
# AI Control Center (usage, budget, cache, failures, audit, unresolved alerts)
# ---------------------------------------------------------------------------
@super_admin_bp.route('/ai-control')
@login_required
@roles_required('SuperAdmin')
def ai_control():
    from app.models import AIUsageLog, ClinicalAlert
    from app.services.ai import platform
    from app.services.ai.copilot import predictive_catalogue
    from app.services.alerts import escalation_thresholds
    stats = platform.usage_stats()
    cache = platform.cache_stats()
    recent = AIUsageLog.query.order_by(AIUsageLog.created_at.desc()).limit(40).all()
    unresolved = (ClinicalAlert.query
                  .filter(ClinicalAlert.status.in_(('OPEN', 'ACKNOWLEDGED', 'IN_PROGRESS')),
                          ClinicalAlert.severity.in_(('CRITICAL', 'HIGH')))
                  .order_by(ClinicalAlert.created_at.desc()).limit(30).all())
    return render_template('super_admin/ai_control.html', title='AI Control Center', stats=stats,
                           cache=cache, recent=recent, unresolved=unresolved,
                           predictive=predictive_catalogue({'SuperAdmin'}),
                           escalation=escalation_thresholds())


@super_admin_bp.route('/ai-control/clear-cache', methods=['POST'])
@login_required
@roles_required('SuperAdmin')
def ai_cache_clear():
    from app.models import AICacheEntry
    n = AICacheEntry.query.delete()
    db.session.commit()
    log_activity('AI_CACHE_CLEAR', 'ai_cache', None, f'{n} entries removed')
    db.session.commit()
    flash(f'AI cache cleared ({n} entries).', 'success')
    return redirect(url_for('super_admin.ai_control'))
