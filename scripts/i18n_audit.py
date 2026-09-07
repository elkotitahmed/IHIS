"""Arabic completeness audit.

Finds visible, hard-coded English text in templates that has no Arabic
branch, i.e. text nodes outside ``{{ … }}`` / ``{% … %}`` that contain
letters and are not wrapped in a ``'…' if g.lang == 'ar' else '…'`` switch.
Prints a per-template count and the strings, most offenders first.

    python scripts/i18n_audit.py            # summary
    python scripts/i18n_audit.py --show 20  # also list strings for the top 20 templates
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, 'app', 'templates')
IGNORE_WORDS = {'ihis', 'ok', 'id', 'mrn', 'bp', 'hr', 'rr', 'spo2', 'spo₂', 'temp', 'ai', 'icd', 'icd-10', 'fhir', 'pdf',
                'csv', 'json', 'api', 'news2', 'qsofa', 'lace', 'am', 'pm', 'kg', 'cm', 'mg', 'ml', 'x', 'n/a', 'na', 'iv', 'im',
                'ecg', 'ct', 'mri', 'us', 'cbc', 'hba1c', 'soap', 'rx', 'dx', 'er', 'ed', 'icu', 'yes', 'no', 'a', 'c', 'v', 'p', 'u',
                'sys', 'dia', 'sms', 'email', 'url', 'utc', 'gemini', 'ddinter', 'openfda', 'rxnorm', 'rxcui', 'auc',
                'mmhg', 'bpm', 'min', 'mar', 'abn', 'sat', 'o2', 'middot', 'nbsp', 'amp', 'mg/dl', 'mmol', 'kpa', 'iu/l', 'g/dl',
                'lbs', 'bmi', 'pt', 'inr', 'aptt', 'ph', 'rbc', 'wbc', 'plt', 'hb', 'ldl', 'hdl', 'tsh', 'crp', 'esr', 'bun', 'egfr',
                'alt', 'ast', 'na', 'k', 'ca', 'mrn-', 'tel', 'fax', 'dob', 'yrs', 'yr', 'hrs', 'hr', 'mins', 'sec', 'ml/h', 'gtt'}


def strip_jinja(s):
    s = re.sub(r'\{#.*?#\}', ' ', s, flags=re.S)
    s = re.sub(r'\{\{.*?\}\}', ' ', s, flags=re.S)
    s = re.sub(r'\{%.*?%\}', ' ', s, flags=re.S)
    s = re.sub(r'<script.*?</script>', ' ', s, flags=re.S | re.I)
    s = re.sub(r'<style.*?</style>', ' ', s, flags=re.S | re.I)
    return s


def visible_text(html):
    parts = []
    for m in re.finditer(r'>([^<>]+)<', html):
        t = re.sub(r'\s+', ' ', m.group(1)).strip()
        if t:
            parts.append(t)
    # attribute text that users see
    for m in re.finditer(r'(?:placeholder|title|aria-label|alt)="([^"{}]+)"', html):
        parts.append(m.group(1).strip())
    return parts


def is_english(t):
    if not re.search(r'[A-Za-z]{3,}', t):
        return False
    if re.search(r'[؀-ۿ]', t):
        return False
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’\-]*", t)]
    if not words:
        return False
    if all(w.lower() in IGNORE_WORDS for w in words):
        return False
    return True


def audit():
    rows = []
    for dp, _, fs in os.walk(TPL):
        for f in fs:
            if not f.endswith('.html'):
                continue
            p = os.path.join(dp, f)
            raw = open(p, encoding='utf-8').read()
            strings = [t for t in visible_text(strip_jinja(raw)) if is_english(t)]
            uniq = sorted(set(strings), key=strings.index)
            rows.append((len(uniq), os.path.relpath(p, TPL).replace('\\', '/'), uniq))
    rows.sort(reverse=True)
    return rows


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    show = int(sys.argv[sys.argv.index('--show') + 1]) if '--show' in sys.argv else 0
    rows = audit()
    total = sum(r[0] for r in rows)
    print(f'templates: {len(rows)}   templates with English-only text: {sum(1 for r in rows if r[0])}   strings: {total}')
    for n, name, uniq in rows[:show]:
        print(f'\n== {name} ({n})')
        for s in uniq:
            print('   ', s[:100])
    if not show:
        for n, name, _ in rows[:40]:
            if n:
                print(f'{n:>4}  {name}')
