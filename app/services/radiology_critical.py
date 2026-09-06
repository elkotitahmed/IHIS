"""Radiology critical-finding engine.

Radiology study → radiologist report → **analysis** → clinical alert →
urgent task → notification of the responsible physician and care team →
acknowledgement → documented action → resolution → audit.

Three detectors, in order of authority:

1. **Deterministic rules** (this module) — keyword rules with a simple
   negation window. Authoritative: a rule hit always raises an alert.
2. **Local ML classifier** (``RadiologyCriticalAI``, the supplied
   negation-aware model) — AI-assisted; raises an alert when it predicts a
   critical finding.
3. **Gemini explanation** (optional, budgeted) — only *explains* what was
   flagged and suggests a review action. It never decides.

The radiologist's report is never modified. Alerts are de-duplicated per
report, carry an ``ai_assisted`` flag, a confidence and a rationale, and are
assigned to the ordering physician.
"""
import re

from flask import current_app

from app import db
from app.models import CriticalFindingNotification, Task
from app.services import alerts as alert_svc
from app.services import tasks as task_svc
from app.services.ai import platform
from app.services.notifications import care_team_user_ids, notify, notify_users
from app.services.timeline import record_event
from app.utils import utcnow

# (regex, finding, severity, recommended action)
RULES = [
    (r'tension pneumothorax|pneumothorax', 'Pneumothorax', 'CRITICAL', 'Immediate clinical review; consider chest drain.'),
    (r'aortic dissection|dissection of the aorta|dissection flap', 'Aortic dissection', 'CRITICAL', 'Emergency vascular/cardiothoracic referral.'),
    (r'pulmonary embol|filling defect.{0,40}pulmonary arter', 'Pulmonary embolism', 'CRITICAL', 'Urgent anticoagulation assessment.'),
    (r'intracranial (h(a)?emorrhage|bleed)|subdural|subarachnoid|epidural h(a)?ematoma|intracerebral', 'Intracranial haemorrhage', 'CRITICAL', 'Immediate neurosurgical review.'),
    (r'free (intraperitoneal )?air|pneumoperitoneum|perforat', 'Free air / perforation', 'CRITICAL', 'Urgent surgical review.'),
    (r'ectopic pregnancy', 'Ectopic pregnancy', 'CRITICAL', 'Urgent gynaecology review.'),
    (r'midline shift|brain herniation|uncal herniation', 'Mass effect / herniation', 'CRITICAL', 'Immediate neurosurgical review.'),
    (r'ischemic bowel|ischaemic bowel|mesenteric ischemia|mesenteric ischaemia|volvulus', 'Bowel ischaemia / volvulus', 'CRITICAL', 'Urgent surgical review.'),
    (r'ruptured (aneurysm|aaa)|aneurysm .{0,20}rupture', 'Ruptured aneurysm', 'CRITICAL', 'Emergency vascular referral.'),
    (r'cord compression|cauda equina', 'Spinal cord compression', 'CRITICAL', 'Emergency spinal referral.'),
    (r'bowel obstruction|small bowel obstruction|large bowel obstruction', 'Bowel obstruction', 'HIGH', 'Urgent surgical assessment.'),
    (r'deep vein thrombosis|dvt', 'Deep vein thrombosis', 'HIGH', 'Anticoagulation assessment today.'),
    (r'displaced fracture|unstable fracture|fracture.{0,30}displace', 'Displaced fracture', 'HIGH', 'Orthopaedic review today.'),
    (r'(new|suspicious|spiculated|enhancing) (mass|lesion)|malignan|carcinoma|metasta', 'Suspicious mass / malignancy', 'HIGH', 'Expedited specialist referral and MDT discussion.'),
    (r'abscess|empyema', 'Abscess / empyema', 'HIGH', 'Source control assessment; consider drainage.'),
    (r'large pleural effusion|massive effusion|tamponade|large pericardial effusion', 'Large effusion / tamponade', 'HIGH', 'Urgent clinical review.'),
    (r'hydronephrosis|obstructing (calculus|stone)', 'Obstructive uropathy', 'HIGH', 'Urology review; check renal function.'),
    (r'fracture', 'Fracture', 'MODERATE', 'Orthopaedic assessment and immobilisation.'),
    (r'consolidation|pneumonia|infiltrate', 'Consolidation / pneumonia', 'MODERATE', 'Correlate clinically; consider antibiotics.'),
    (r'pulmonary nodule|lung nodule|nodule', 'Nodule requiring follow-up', 'MODERATE', 'Arrange follow-up imaging per nodule guidelines.'),
    (r'cardiomegaly|pulmonary (o)?edema', 'Cardiomegaly / pulmonary oedema', 'MODERATE', 'Cardiac assessment.'),
    (r'incidental', 'Incidental finding', 'INFORMATIONAL', 'Document and arrange routine follow-up if needed.'),
]
_NEGATION = re.compile(r'\b(no|without|negative for|rule[sd]? out|excluded?|absence of|not seen|free of|unremarkable for|resolved)\b',
                       re.I)
SEVERITY_RANK = {'INFORMATIONAL': 0, 'MODERATE': 1, 'HIGH': 2, 'CRITICAL': 3}
ALERT_SEVERITY = {'INFORMATIONAL': 'INFO', 'MODERATE': 'MODERATE', 'HIGH': 'HIGH', 'CRITICAL': 'CRITICAL'}


def _negated(text, start):
    window = text[max(0, start - 45):start]
    return bool(_NEGATION.search(window))


def rule_scan(text):
    """Deterministic keyword scan with a negation window. Returns findings."""
    text = (text or '')
    lowered = text.lower()
    found, seen = [], set()
    for pattern, finding, severity, action in RULES:
        for m in re.finditer(pattern, lowered):
            if finding in seen:
                break
            if _negated(lowered, m.start()):
                continue
            snippet = text[max(0, m.start() - 40): m.end() + 40].replace('\n', ' ').strip()
            found.append({'finding': finding, 'severity': severity, 'action': action,
                          'source': 'rule', 'confidence': None,
                          'rationale': f'Report text matches "{m.group(0)}" without negation: "…{snippet}…"'})
            seen.add(finding)
            break
    return found


def ml_scan(findings, impression, study_type=''):
    """The supplied negation-aware classifier; None when unavailable."""
    try:
        from app.services.radiology_critical_ai import RadiologyCriticalAI
        return RadiologyCriticalAI().analyze(findings=findings or '', impression=impression or '',
                                             study_type=study_type or '')
    except Exception as exc:  # noqa: BLE001 - the report must never be lost to an AI failure
        try:
            current_app.logger.warning('Radiology critical AI unavailable: %s: %s',
                                       type(exc).__name__, exc)
        except Exception:  # noqa: BLE001
            pass
        return None


def evaluate_report(order, report, manual_text=None, manual=False):
    """Combine rules + ML (+ manual flag). Returns a structured evaluation."""
    text = f"{report.findings or ''}\n{report.impression or ''}"
    findings = rule_scan(text)
    study = order.imaging_type.name if order.imaging_type else ''
    ml = ml_scan(report.findings, report.impression, study)
    if ml and ml.get('critical_finding'):
        priority = (ml.get('priority') or 'URGENT').upper()
        sev = 'CRITICAL' if priority == 'CRITICAL' else 'HIGH'
        label = ml.get('finding_type') or 'Critical finding (model)'
        if not any(f['finding'].lower() == str(label).lower() for f in findings):
            findings.append({'finding': str(label), 'severity': sev, 'source': 'model',
                             'confidence': float(ml.get('confidence') or 0) or None,
                             'action': 'Immediate physician review of the report.',
                             'rationale': ml.get('message') or 'Local negation-aware classifier predicted a critical result.'})
    if manual:
        findings.append({'finding': manual_text or 'Critical finding flagged by radiologist',
                         'severity': 'CRITICAL', 'source': 'radiologist', 'confidence': 1.0,
                         'action': 'Immediate physician review.', 'rationale': 'Flagged manually by the reporting radiologist.'})
    findings.sort(key=lambda f: -SEVERITY_RANK.get(f['severity'], 0))
    top = findings[0]['severity'] if findings else None
    return {'findings': findings, 'severity': top, 'ml': ml,
            'critical': bool(findings) and SEVERITY_RANK.get(top, 0) >= SEVERITY_RANK['HIGH'],
            'ai_assisted': any(f['source'] in ('model', 'rule') for f in findings)}


def raise_alert(order, report, evaluation, actor_id=None):
    """Create/refresh the alert, urgent task, notifications and audit trail for
    a HIGH/CRITICAL evaluation. Idempotent per report. Returns the alert or
    None when nothing needed raising. Never touches the report text."""
    if not evaluation.get('critical'):
        return None
    top = evaluation['findings'][0]
    severity = ALERT_SEVERITY[top['severity']]
    study = order.imaging_type.name if order.imaging_type else 'study'
    responsible = order.doctor.user_id if order.doctor and order.doctor.user_id else None
    detail = '; '.join(f"{f['finding']} ({f['severity']}, {f['source']})" for f in evaluation['findings'][:4])
    alert = alert_svc.ensure_open_alert(
        order.patient_id, 'CRITICAL_RADIOLOGY', severity=severity,
        title=f"{top['finding']} on {study}",
        message=(f"Order #{order.id}, report #{report.id}. {detail}. "
                 f"Recommended: {top['action']}"),
        source_type='radiology_report', source_id=report.id)
    alert.ai_assisted = bool(evaluation.get('ai_assisted'))
    alert.confidence = top.get('confidence')
    alert.rationale = '\n'.join(f"[{f['source']}] {f['rationale']}" for f in evaluation['findings'][:4])
    if responsible and not alert.assigned_to:
        alert.assigned_to = responsible
    db.session.flush()

    if not task_svc.open_tasks_for_resource('clinical_alert', alert.id):
        task_svc.create_task(
            title=f"URGENT: review critical radiology finding — {top['finding']}",
            description=(f"{study}, order #{order.id}. Acknowledge the alert, review the report "
                         f"and document the action taken. Recommended: {top['action']}"),
            task_type='CRITICAL_RESULT', department='Clinical', patient_id=order.patient_id,
            assigned_to=responsible, assigned_role=None if responsible else 'Doctor',
            priority='URGENT', related_resource_type='clinical_alert', related_resource_id=alert.id)

    title = f"CRITICAL RADIOLOGY FINDING: {top['finding']}"
    body = (f"Patient #{order.patient_id} — {study} (order #{order.id}). "
            f"{top['action']} Acknowledge the alert and document your action.")
    recipients = set()
    if responsible:
        notify(responsible, title, body, notification_type='critical',
               entity_type='clinical_alert', entity_id=alert.id)
        recipients.add(responsible)
    team = [u for u in care_team_user_ids(order.patient_id) if u not in recipients]
    if team:
        notify_users(team, title, body, notification_type='critical',
                     entity_type='clinical_alert', entity_id=alert.id)
    if not recipients and not team:
        from app.services.notifications import notify_role
        notify_role('Doctor', title, body, notification_type='critical',
                    entity_type='clinical_alert', entity_id=alert.id)

    existing = CriticalFindingNotification.query.filter_by(order_id=order.id, acknowledged=False).first()
    if existing is None:
        db.session.add(CriticalFindingNotification(
            order_id=order.id, finding=top['finding'][:250],
            severity='Critical' if severity == 'CRITICAL' else 'Urgent',
            identified_by=actor_id, responsible_clinician_id=responsible,
            notification_method='EMR Message', notification_time=utcnow(),
            recipient_name=(order.doctor.user.full_name if order.doctor and order.doctor.user else None)))
    record_event(order.patient_id, 'ALERT', f"Critical radiology finding: {top['finding']}",
                 f"{study} · severity {severity} · {'AI-assisted' if alert.ai_assisted else 'radiologist-flagged'}",
                 source_type='clinical_alert', source_id=alert.id, department='Radiology')
    try:
        from app.routes.decorators import log_activity
        log_activity('CRITICAL_RADIOLOGY_ALERT', 'clinical_alert', alert.id,
                     f'{top["finding"]} severity={severity} ai_assisted={alert.ai_assisted}')
    except Exception:  # noqa: BLE001
        pass
    return alert


def ai_explain(order, report, evaluation, patient_id=None):
    """Optional Gemini explanation: why flagged, what to review. Budgeted and cached."""
    from app.services.ai.platform import data_block
    study = order.imaging_type.name if order.imaging_type else 'study'
    flagged = '; '.join(f"{f['finding']} [{f['severity']}, {f['source']}]" for f in evaluation['findings']) or 'nothing flagged'
    prompt = (
        "TASK: For the treating physician, list (1) potential important findings in this radiology report, "
        "(2) the severity you would assign each (CRITICAL/HIGH/MODERATE/INFORMATIONAL), (3) one sentence on why, "
        "(4) a recommended review action. Max 6 bullets, plain text, no HTML. The deterministic flags below are "
        "authoritative and must not be contradicted; you may add lower-severity items only.\n"
        f"Deterministic flags: {flagged}\n"
        f"{data_block('report', f'STUDY: {study}\nFINDINGS: {report.findings or ''}\nIMPRESSION: {report.impression or ''}', 6000)}")
    return platform.run_ai('radiology.ai_assist', patient_id or order.patient_id,
                           {'report_id': report.id, 'text': (report.findings or '') + (report.impression or ''),
                            'flags': flagged},
                           prompt, heavy=False, max_tokens=500)


def alert_for_report(report_id):
    from app.models import ClinicalAlert
    return (ClinicalAlert.query.filter_by(source_type='radiology_report', source_id=report_id)
            .order_by(ClinicalAlert.created_at.desc()).first())


def open_task_for_alert(alert_id):
    return (Task.query.filter_by(related_resource_type='clinical_alert', related_resource_id=alert_id)
            .order_by(Task.created_at.desc()).first())
