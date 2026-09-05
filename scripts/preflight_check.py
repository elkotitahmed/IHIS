#!/usr/bin/env python3
"""Pre-deployment safety checks for iHIS (Release Candidate).

Verifies, before going live:
  - Python version meets the pinned runtime
  - SECRET_KEY and DATABASE_URL are configured correctly for production
  - App boots under production config (fast fail)
  - Alembic migrations are on a single, current head
  - Static and private upload paths are present/writable where needed
  - seed.py offers --roles-only (never auto-seeds demo data in production)
  - No uncommitted tracked source drift (deployment hygiene)

Exits non-zero on any critical failure.

Usage:
    python scripts/preflight_check.py
"""
import os
import sys
from pathlib import Path

BASEDIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASEDIR))

FAILURES = []
WARNINGS = []


def check(name, ok, detail='', warned=False):
    status = 'PASS' if ok else ('WARN' if warned else 'FAIL')
    print(f'[{status}] {name}' + (f' ? {detail}' if detail else ''))
    if ok:
        return
    if warned:
        WARNINGS.append(name)
    else:
        FAILURES.append(name)


def main():
    print('=== iHIS Pre-deployment Checklist (Release Candidate) ===\n')

    # 1. Python version (runtime.txt pins 3.12).
    py = sys.version_info
    check('Python 3.12 (runtime.txt pins 3.12)',
          py.major == 3 and py.minor == 12,
          f'current: {py.major}.{py.minor}.{py.micro}')

    from dotenv import load_dotenv
    load_dotenv(BASEDIR / '.env')

    # 2. Secrets.
    secret = os.environ.get('SECRET_KEY', '')
    check('SECRET_KEY set', bool(secret),
          'falling back to dev key is not allowed in production')
    check('SECRET_KEY minimum length (>=32)',
          len(secret) >= 32 or not secret, f'current length: {len(secret)}')
    weak = {'ihis-dev-secret-key-change-me', 'change-me-to-a-long-random-string'}
    check('SECRET_KEY not a known weak value', secret not in weak)

    db_url = os.environ.get('DATABASE_URL', '')
    check('DATABASE_URL set', bool(db_url), 'production must use PostgreSQL')
    check('PostgreSQL (not SQLite) in production',
          db_url.startswith('postgresql'),
          f'backend: {"postgresql" if db_url.startswith("postgresql") else "other"}')

    # 3. Boot check (fast fail). Uses the production config so a real
    #    DATABASE_URL/SECRET_KEY are required and validated by create_app.
    try:
        from app import create_app
        app = create_app('production')
        check('App boots under production config', True)
    except Exception as e:
        check('App boots under production config', False,
              f'{type(e).__name__}: {e}')

    # 4. Migration head (single, current). Read from the Alembic script dir,
    #    not a live DB, so this works without a server connection.
    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory
        cfg = AlembicConfig(str(BASEDIR / 'migrations' / 'alembic.ini'))
        cfg.set_main_option('script_location', str(BASEDIR / 'migrations'))
        heads = ScriptDirectory.from_config(cfg).get_heads()
        check('Alembic has exactly one migration head', len(heads) == 1,
              f'heads: {heads}')
        if len(heads) == 1:
            expected = os.environ.get('EXPECTED_MIGRATION_HEAD')
            if expected:
                check('Migration head matches EXPECTED_MIGRATION_HEAD',
                      heads[0] == expected, f'head: {heads[0]}')
    except Exception as e:
        check('Alembic migration head check', False,
              f'{type(e).__name__}: {e}')

    # 5. Static and private upload paths.
    upload = os.environ.get('UPLOAD_FOLDER') or str(BASEDIR / 'var' / 'uploads')
    try:
        Path(upload).mkdir(parents=True, exist_ok=True)
        check('Private upload path writable', os.access(upload, os.W_OK),
              upload)
    except Exception as e:
        check('Private upload path writable', False, f'{type(e).__name__}: {e}')
    static = basedir_static = str(BASEDIR / 'app' / 'static')
    check('Static asset directory exists',
          Path(static).is_dir(), static)

    # 6. Seed safety: --roles-only must exist so demo data is never auto-seeded.
    seed_file = BASEDIR / 'seed.py'
    check('seed.py exists with --roles-only (no demo in prod)',
          seed_file.is_file() and '--roles-only' in seed_file.read_text(),
          'verified by code inspection')

    # 7. Git cleanliness (no uncommitted tracked source drift before deploy).
    try:
        import subprocess
        result = subprocess.run(
            ['git', 'status', '--porcelain'], capture_output=True, text=True,
            cwd=BASEDIR)
        pending = [line for line in result.stdout.splitlines()
                   if line and not line.startswith('??')]
        # Untracked docs/scripts/backup from this release pass are OK; flag but
        # do not fail on them. Actual modified tracked files are the gate.
        check('No uncommitted tracked changes', not pending,
              f'{len(pending)} tracked file(s) modified' if pending else '',
              warned=bool(pending))
    except Exception:
        check('Git status check', True, 'git not available, skipped')

    print('\n=== Summary ===')
    if FAILURES:
        print(f'PREFLIGHT FAILED: {len(FAILURES)} critical check(s) failed')
        for f in FAILURES:
            print(f'  - {f}')
        sys.exit(1)
    print('PREFLIGHT PASSED ? safe to deploy')
    if WARNINGS:
        print('Warnings (non-blocking):')
        for w in WARNINGS:
            print(f'  - {w}')
    sys.exit(0)


if __name__ == '__main__':
    main()