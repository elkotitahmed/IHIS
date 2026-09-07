"""Drug–drug interaction reference (DDInter, ddinter.scbdd.com).

Data: ``app/data/ddinter.tsv`` — ~160k curated pairs with a severity level
(Major / Moderate / Minor / Unknown).  Licence: CC BY-NC-SA 4.0 (non-commercial
use with attribution); see ``app/data/DATA_LICENSES.md``.

The table is loaded lazily once per process and matched on the lowercase
generic name (brand names are first mapped through the local formulary by the
caller).  Deterministic: no AI is involved.
"""
import io
import os
import re
import threading

_lock = threading.Lock()
_pairs = None          # {(a, b): level} with a < b
_index = None          # {drug: set(other drugs)}

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'ddinter.tsv')
SEVERITY_ORDER = {'Major': 3, 'Moderate': 2, 'Minor': 1, 'Unknown': 0}


def _load():
    global _pairs, _index
    if _pairs is not None:
        return
    with _lock:
        if _pairs is not None:
            return
        pairs, index = {}, {}
        if os.path.isfile(DATA_FILE):
            with io.open(DATA_FILE, encoding='utf-8') as f:
                for ln in f:
                    if not ln or ln[0] == '#':
                        continue
                    parts = ln.rstrip('\n').split('\t')
                    if len(parts) < 3:
                        continue
                    a, b, lvl = parts[0], parts[1], parts[2]
                    pairs[(a, b)] = lvl
                    index.setdefault(a, set()).add(b)
                    index.setdefault(b, set()).add(a)
        _pairs, _index = pairs, index


def available():
    _load()
    return bool(_pairs)


def size():
    _load()
    return len(_pairs)


# Common INN / regional names → the name DDInter uses
ALIASES = {
    'aspirin': 'acetylsalicylic acid', 'asa': 'acetylsalicylic acid',
    'paracetamol': 'acetaminophen', 'salbutamol': 'albuterol',
    'adrenaline': 'epinephrine', 'noradrenaline': 'norepinephrine',
    'frusemide': 'furosemide', 'glibenclamide': 'glyburide',
    'amoxycillin': 'amoxicillin', 'co-amoxiclav': 'amoxicillin', 'augmentin': 'amoxicillin',
    'co-trimoxazole': 'sulfamethoxazole', 'glucophage': 'metformin',
    'lignocaine': 'lidocaine', 'ciclosporin': 'cyclosporine', 'beclometasone': 'beclomethasone',
}


def normalise(name):
    """'Metformin 500 mg' -> 'metformin'; 'Amoxicillin/Clavulanate' -> ['amoxicillin', 'clavulanate']."""
    n = (name or '').lower()
    n = re.sub(r'\b\d+(\.\d+)?\s*(mg|mcg|g|ml|iu|%)\b.*$', '', n)      # strip strength and after
    n = re.sub(r'\((.*?)\)', ' ', n)
    parts = [p.strip() for p in re.split(r'[/+,]', n) if p.strip()]
    out = []
    for p in parts:
        p = re.sub(r'\b(tablets?|capsules?|injection|syrup|oral|iv|im|sodium|hydrochloride|hcl|sulfate|sulphate|potassium|calcium)\b', '', p)
        p = re.sub(r'\s+', ' ', p).strip()
        p = ALIASES.get(p, p)
        if p:
            out.append(p)
    return out or ([n.strip()] if n.strip() else [])


def known(name):
    _load()
    return any(p in _index for p in normalise(name))


def check(names):
    """Interactions among a list of medication names.

    Returns a list of dicts sorted by severity:
    ``{'a', 'b', 'level', 'source': 'DDInter'}`` using the display names given.
    """
    _load()
    if not _pairs:
        return []
    norm = []
    for display in names or []:
        for token in normalise(display):
            if token in _index:
                norm.append((token, display))
    out, seen = [], set()
    for i in range(len(norm)):
        for j in range(i + 1, len(norm)):
            ta, da = norm[i]; tb, db_ = norm[j]
            if ta == tb:
                continue
            key = tuple(sorted((ta, tb)))
            if key in seen:
                continue
            lvl = _pairs.get(key)
            if lvl:
                seen.add(key)
                out.append({'a': da, 'b': db_, 'level': lvl, 'source': 'DDInter'})
    out.sort(key=lambda r: -SEVERITY_ORDER.get(r['level'], 0))
    return out


def interactions_for(name, limit=50):
    """All documented interaction partners of one drug (for the reference page)."""
    _load()
    rows = []
    for token in normalise(name):
        for other in sorted(_index.get(token, ())):
            key = tuple(sorted((token, other)))
            rows.append({'drug': other, 'level': _pairs.get(key, 'Unknown')})
    rows.sort(key=lambda r: (-SEVERITY_ORDER.get(r['level'], 0), r['drug']))
    return rows[:limit]
