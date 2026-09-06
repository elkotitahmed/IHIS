"""Deterministic inbox prioritisation ("What needs my attention first?").

The ranking below is rule-based and authoritative. AI may add a short
narrative on top (``ai_priority_narrative``) but can never reorder or hide a
clinical-critical item.
"""
from app.models import ClinicalAlert, LabOrder, LabResult, Message, Referral, Task
from app.services.ai import platform
from app.utils import utcnow

RANK = {'critical_alert': 0, 'critical_lab': 1, 'high_alert': 2, 'urgent_task': 3,
        'urgent_referral': 4, 'overdue_task': 5, 'abnormal_lab': 6, 'task': 7,
        'referral': 8, 'message': 9}


def build_items(user, patient_ids, limit=40):
    """Collect candidate items for the user and rank them deterministically."""
    pids = sorted(patient_ids) if patient_ids else [-1]
    now = utcnow()
    items = []

    def _pname(p):
        return p.user.full_name if p and p.user else (f'Patient #{p.id}' if p else '')

    for a in (ClinicalAlert.query.filter(ClinicalAlert.patient_id.in_(pids),
                                         ClinicalAlert.status.in_(('OPEN', 'ACKNOWLEDGED', 'IN_PROGRESS')),
                                         ClinicalAlert.severity.in_(('CRITICAL', 'HIGH')))
              .order_by(ClinicalAlert.created_at.desc()).limit(20).all()):
        kind = 'critical_alert' if a.severity == 'CRITICAL' else 'high_alert'
        items.append({'kind': kind, 'rank': RANK[kind], 'title': a.title, 'patient': _pname(a.patient),
                      'patient_id': a.patient_id, 'when': a.created_at,
                      'url': f'/clinical/alerts/{a.id}', 'why': f'{a.severity} alert, {a.status.lower()}'
                      + (' (AI-assisted)' if a.ai_assisted else '')})
    for o in (LabOrder.query.join(LabResult, LabResult.order_id == LabOrder.id)
              .filter(LabOrder.patient_id.in_(pids), LabResult.status.in_(('Verified', 'Locked', 'Finalized')),
                      LabResult.is_abnormal.is_(True))
              .order_by(LabOrder.order_date.desc()).limit(30).all()):
        r = o.result
        kind = 'critical_lab' if r.is_critical else 'abnormal_lab'
        items.append({'kind': kind, 'rank': RANK[kind],
                      'title': f"{o.test.test_name if o.test else 'Lab'}: {r.result_value} {r.result_unit or ''}",
                      'patient': _pname(o.patient), 'patient_id': o.patient_id, 'when': r.result_date or o.order_date,
                      'url': f'/clinical/inbox', 'why': 'critical value' if r.is_critical else 'abnormal result'})
    for t in (Task.query.filter(Task.assigned_to == user.id,
                                Task.status.in_(('NEW', 'ASSIGNED', 'IN_PROGRESS')))
              .order_by(Task.due_at.asc().nulls_last()).limit(30).all()):
        overdue = bool(t.due_at and t.due_at < now)
        kind = 'urgent_task' if t.priority == 'URGENT' else 'overdue_task' if overdue else 'task'
        items.append({'kind': kind, 'rank': RANK[kind], 'title': t.title, 'patient': _pname(t.patient),
                      'patient_id': t.patient_id, 'when': t.due_at or t.created_at, 'url': f'/tasks/{t.id}',
                      'why': ('urgent' if t.priority == 'URGENT' else 'overdue' if overdue else t.status.lower())})
    for r in (Referral.query.filter(Referral.patient_id.in_(pids), Referral.status.in_(('Pending', 'SENT', 'IN_REVIEW')))
              .order_by(Referral.created_at.desc()).limit(20).all()):
        kind = 'urgent_referral' if (r.urgency or '').lower() in ('urgent', 'emergency') else 'referral'
        items.append({'kind': kind, 'rank': RANK[kind], 'title': f"Referral to {r.to_specialty or 'specialist'}",
                      'patient': _pname(r.patient), 'patient_id': r.patient_id, 'when': r.created_at,
                      'url': '/care/referrals', 'why': (r.urgency or 'routine').lower() + ' referral, ' + r.status.lower()})
    for m in (Message.query.filter_by(receiver_id=user.id, is_read=False)
              .order_by(Message.sent_at.desc()).limit(10).all()):
        items.append({'kind': 'message', 'rank': RANK['message'], 'title': m.subject or 'Message',
                      'patient': m.sender.full_name if m.sender else '', 'patient_id': None, 'when': m.sent_at,
                      'url': '/clinical/inbox#messages', 'why': 'unread message'})
    items.sort(key=lambda i: (i['rank'], -(i['when'].timestamp() if i['when'] else 0)))
    return items[:limit]


def ai_priority_narrative(items, user_id):
    """Optional AI narrative over the deterministic ranking (never reorders)."""
    top = [{'rank': i['rank'], 'kind': i['kind'], 'title': i['title'], 'why': i['why']} for i in items[:12]]
    prompt = ('TASK: In max 6 bullets, tell the physician what needs attention first and why, following the '
              'given order exactly (do not reorder, do not drop critical items). Plain text.\n'
              f"{platform.data_block('ranked_items', str(top), 4000)}")
    return platform.run_ai('inbox.priority', None, {'user': user_id, 'items': top}, prompt, max_tokens=350)
