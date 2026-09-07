"""No-show risk for upcoming appointments, trained on this hospital's own history.

Logistic regression (scikit-learn) over simple, explainable features:
weekday, hour, lead time (days between booking and the visit), visit type,
priority, specialty and the patient's own past no-show rate.  Refuses to
predict when the history is too small (< MIN_ROWS completed/no-show visits) and
says so, instead of inventing a number.  Retrained lazily at most once an hour.
"""
import threading
import time
from datetime import datetime

MIN_ROWS = 150
_lock = threading.Lock()
_state = {'model': None, 'trained_at': 0.0, 'rows': 0, 'auc': None, 'base_rate': None}
RETRAIN_SECONDS = 3600


def _features(a, past_rate):
    sched = a.scheduled_at
    lead = (sched - a.created_at).days if (sched and a.created_at) else 0
    return [
        sched.weekday() if sched else 0,
        sched.hour if sched else 9,
        max(min(lead, 60), 0),
        1 if (a.visit_type or '') == 'WalkIn' else 0,
        1 if (a.priority or 'Normal') != 'Normal' else 0,
        past_rate,
    ]


def _patient_rates(rows):
    seen = {}
    for a in rows:
        d = seen.setdefault(a.patient_id, [0, 0])
        d[1] += 1
        if a.status == 'NoShow':
            d[0] += 1
    return {pid: (ns / n if n else 0.0) for pid, (ns, n) in seen.items()}


def train(force=False):
    """(Re)train from Appointment history. Returns the state dict."""
    from app.models import Appointment
    now = time.time()
    if _state['model'] is not None and not force and now - _state['trained_at'] < RETRAIN_SECONDS:
        return _state
    with _lock:
        rows = Appointment.query.filter(Appointment.status.in_(['Completed', 'NoShow'])).all()
        _state.update(rows=len(rows), trained_at=now)
        if len(rows) < MIN_ROWS:
            _state.update(model=None, auc=None, base_rate=None)
            return _state
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.metrics import roc_auc_score
            from sklearn.model_selection import cross_val_predict
        except Exception:  # noqa: BLE001
            _state.update(model=None)
            return _state
        rates = _patient_rates(rows)
        X = [_features(a, rates.get(a.patient_id, 0.0)) for a in rows]
        y = [1 if a.status == 'NoShow' else 0 for a in rows]
        if len(set(y)) < 2:
            _state.update(model=None, base_rate=sum(y) / len(y))
            return _state
        clf = LogisticRegression(max_iter=500, class_weight='balanced')
        try:
            pred = cross_val_predict(clf, X, y, cv=5, method='predict_proba')[:, 1]
            auc = round(float(roc_auc_score(y, pred)), 3)
        except Exception:  # noqa: BLE001
            auc = None
        clf.fit(X, y)
        _state.update(model=clf, auc=auc, base_rate=round(sum(y) / len(y), 3), rates=rates)
        return _state


def predict(appointments):
    """Return {appointment_id: probability} for upcoming appointments, or {} when not trained."""
    st = train()
    if st['model'] is None:
        return {}
    rates = st.get('rates') or {}
    X = [_features(a, rates.get(a.patient_id, 0.0)) for a in appointments]
    if not X:
        return {}
    probs = st['model'].predict_proba(X)[:, 1]
    return {a.id: round(float(p), 3) for a, p in zip(appointments, probs)}


def status():
    st = train()
    return {'trained': st['model'] is not None, 'rows': st['rows'], 'min_rows': MIN_ROWS,
            'auc': st['auc'], 'base_rate': st['base_rate']}
