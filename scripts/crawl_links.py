"""Crawl every reachable page as every demo role, follow every internal link,
form GET action and static asset it renders; report anything that is not 2xx/3xx.
Also flag identical (href, text) pairs repeated inside <main> on one page."""
import os, re, sys, collections
from html.parser import HTMLParser
ROOT = r'D:\AI in health care\protoproject\iHIS_Project'
os.chdir(ROOT); sys.path.insert(0, ROOT)
os.environ.setdefault('FLASK_CONFIG', 'development')
from app import create_app
app = create_app('development'); app.config['WTF_CSRF_ENABLED'] = False; app.config['RATELIMIT_ENABLED'] = False
from app import limiter; limiter.enabled = False

ACCOUNTS = ['superadmin@ihis.com', 'admin@ihis.com', 'dr.ahmed@ihis.com', 'nurse@ihis.com', 'lab@ihis.com',
            'radio@ihis.com', 'radtech@ihis.com', 'pharma@ihis.com', 'physio@ihis.com', 'dentist@ihis.com',
            'reception@ihis.com', 'cashier@ihis.com', 'patient@ihis.com']
SKIP = re.compile(r'^(#|javascript:|mailto:|tel:|https?://|//|data:)|/auth/logout|/super-admin/exit-preview|/super-admin/preview/|lang=|\?ai=1|/patient/preview/')

class P(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self.assets = []; self.forms = []; self.in_main = 0; self.main_links = []
        self._cur = None; self._txt = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'main': self.in_main += 1
        if tag == 'a' and a.get('href'):
            self._cur = a['href']; self._txt = []
            self.links.append(a['href'])
        if tag in ('link', 'script', 'img') and (a.get('href') or a.get('src')):
            self.assets.append(a.get('href') or a.get('src'))
        if tag == 'form' and (a.get('method', 'get').lower() == 'get') and a.get('action'):
            self.forms.append(a['action'])
    def handle_data(self, d):
        if self._cur is not None: self._txt.append(d)
    def handle_endtag(self, tag):
        if tag == 'main': self.in_main -= 1
        if tag == 'a' and self._cur is not None:
            if self.in_main: self.main_links.append((self._cur, ' '.join(''.join(self._txt).split())))
            self._cur = None

bad = collections.OrderedDict(); dup = collections.OrderedDict(); pages_total = 0
for email in ACCOUNTS:
    c = app.test_client()
    r = c.post('/auth/login', data={'email': email, 'password': '123456'})
    assert r.status_code in (200, 302), (email, r.status_code)
    seen = set(); queue = [('/dashboard','-'), ('/ai/hub','-'), ('/notifications','-')]
    checked_assets = set()
    while queue and len(seen) < 500:
        url, ref = queue.pop(0)
        path = url.split('#')[0]
        if not path or path in seen or SKIP.search(path): continue
        seen.add(path)
        r = c.get(path, follow_redirects=True)
        pages_total += 1
        if r.status_code >= 400:
            bad.setdefault((path, r.status_code), []).append(f'{email.split("@")[0]} via {ref}'); continue
        if 'text/html' not in (r.content_type or ''): continue
        p = P(); p.feed(r.get_data(as_text=True))
        for h in p.links + p.forms:
            if not SKIP.search(h) and h.startswith('/'): queue.append((h, path))
        for a in p.assets:
            if a.startswith('/') and a not in checked_assets:
                checked_assets.add(a)
                if c.get(a).status_code >= 400: bad.setdefault((a, 'asset'), []).append(path)
        cnt = collections.Counter(p.main_links)
        for (h, t), n in cnt.items():
            if n > 1 and t and not SKIP.search(h):
                dup.setdefault((path, h, t), set()).add(email)
print('PAGES CHECKED', pages_total)
print('\n== NOT OK (>=400) ==')
for (u, st), who in bad.items(): print(f'{st:>5}  {u}   <- {sorted(set(who))[:4]}')
print('\n== SAME LINK+TEXT REPEATED ON ONE PAGE (inside <main>) ==')
for (page, h, t), who in dup.items(): print(f'{page}  ->  {h}  "{t[:40]}"  ({len(who)} roles)')
print('\nBAD:', len(bad), 'DUP:', len(dup))
