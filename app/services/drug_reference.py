"""Official drug reference lookups: openFDA drug labels and NLM RxNorm.

* openFDA (``api.fda.gov/drug/label.json``) — FDA-approved labelling text
  (boxed warning, indications, contraindications, warnings, drug interactions,
  dosage). Free; 1,000 requests/day without a key, 120,000 with ``OPENFDA_API_KEY``.
* RxNorm (``rxnav.nlm.nih.gov``) — normalises a drug name to an RxCUI and the
  ingredient name.

Both are optional, network-only, cached in memory for 12 hours and never
required by any workflow. Text is shown to the clinician as-is (it is not
parsed into rules).
"""
import os
import re
import threading
import time

import requests

TTL = 12 * 3600
_cache = {}
_lock = threading.Lock()
TIMEOUT = 8


def _get(key):
    with _lock:
        v = _cache.get(key)
        if v and v[0] > time.time():
            return v[1]
    return None


def _put(key, value):
    with _lock:
        _cache[key] = (time.time() + TTL, value)
    return value


def _clean(text, limit=1200):
    t = re.sub(r'\s+', ' ', (text or '')).strip()
    return (t[:limit] + '…') if len(t) > limit else t


def rxnorm(name):
    """Return {'rxcui', 'name'} or None."""
    key = ('rx', (name or '').lower())
    hit = _get(key)
    if hit is not None:
        return hit or None
    try:
        r = requests.get('https://rxnav.nlm.nih.gov/REST/rxcui.json', params={'name': name, 'search': 1}, timeout=TIMEOUT)
        ids = (r.json().get('idGroup') or {}).get('rxnormId') or []
        out = {'rxcui': ids[0], 'name': name} if ids else {}
    except Exception:  # noqa: BLE001
        out = {}
    return _put(key, out) or None


def fda_label(name):
    """Return the key labelling sections for a generic name, or None when not found/offline."""
    key = ('fda', (name or '').lower())
    hit = _get(key)
    if hit is not None:
        return hit or None
    params = {'search': f'openfda.generic_name:"{name}"', 'limit': 1}
    api_key = os.environ.get('OPENFDA_API_KEY')
    if api_key:
        params['api_key'] = api_key
    try:
        r = requests.get('https://api.fda.gov/drug/label.json', params=params, timeout=TIMEOUT)
        if r.status_code != 200:
            return _put(key, {}) or None
        res = (r.json().get('results') or [None])[0]
        if not res:
            return _put(key, {}) or None
        fda = res.get('openfda') or {}
        out = {
            'brand': ', '.join((fda.get('brand_name') or [])[:3]),
            'manufacturer': ', '.join((fda.get('manufacturer_name') or [])[:1]),
            'sections': [(title, _clean(' '.join(res.get(field) or [])))
                         for title, field in (('Boxed warning', 'boxed_warning'),
                                              ('Indications and usage', 'indications_and_usage'),
                                              ('Contraindications', 'contraindications'),
                                              ('Warnings and precautions', 'warnings_and_cautions'),
                                              ('Warnings', 'warnings'),
                                              ('Drug interactions', 'drug_interactions'),
                                              ('Dosage and administration', 'dosage_and_administration'),
                                              ('Use in pregnancy', 'pregnancy'))
                         if res.get(field)],
            'effective_time': res.get('effective_time'),
            'source': 'openFDA drug label',
        }
    except Exception:  # noqa: BLE001
        out = {}
    return _put(key, out) or None
