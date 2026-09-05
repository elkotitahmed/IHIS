"""Security header & cookie audit for the Release Candidate (Phase 16/17)."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['DATABASE_URL'] = 'postgresql+psycopg2://u:p@localhost:5432/ihis'
os.environ['SECRET_KEY'] = 'z' * 90
os.environ['FLASK_CONFIG'] = 'production'

from app import create_app

app = create_app('production')
c = app.test_client()

r = c.get('/')
print('GET / status:', r.status_code)
print('Headers present:')
for k in ('Strict-Transport-Security', 'X-Content-Type-Options',
          'X-Frame-Options', 'Referrer-Policy', 'Content-Security-Policy',
          'Set-Cookie'):
    print(f'  {k}: {r.headers.get(k)!r}')

# Login to get a session cookie and inspect flags.
lr = c.post('/auth/login', data={'email': 'nobody@example.com', 'password': 'x'})
print('\nSet-Cookie on login POST:', lr.headers.get('Set-Cookie'))

# Health endpoints
print('\n/health/live:', c.get('/health/live').status_code, c.get('/health/live').get_json())
print('/health/ready against postgres (unreachable) ->', c.get('/health/ready').status_code, c.get('/health/ready').get_json())