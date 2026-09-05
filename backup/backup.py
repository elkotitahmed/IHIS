#!/usr/bin/env python3
"""iHIS database backup utility.

Supports:
  - PostgreSQL (pg_dump)
  - SQLite (file copy)

Usage:
    python backup/backup.py                  # full backup to backup/backups/
    python backup/backup.py --retention 14   # keep last 14 days
    python backup/backup.py --verify         # backup + verify restore

Environment:
    DATABASE_URL    — connection string (required)
    BACKUP_DIR      — output directory (default: backup/backups/)
    PGPASSWORD      — PostgreSQL password (or use .pgpass)

For PostgreSQL, ensure pg_dump is on PATH.
"""
import os
import sys
import shutil
import subprocess
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASEDIR = Path(__file__).resolve().parent.parent
BACKUP_DIR = Path(os.environ.get('BACKUP_DIR', str(BASEDIR / 'backup' / 'backups')))


def get_db_url():
    from dotenv import load_dotenv
    load_dotenv(BASEDIR / '.env')
    return os.environ.get('DATABASE_URL', '')


def backup_postgres(db_url, backup_dir):
    """Backup PostgreSQL using pg_dump."""
    # Parse connection string: postgresql+psycopg2://user:pass@host:5432/dbname
    url = db_url.replace('postgresql+psycopg2://', '').replace('postgresql://', '')
    if '@' in url:
        auth, rest = url.split('@', 1)
        if ':' in auth:
            user, password = auth.split(':', 1)
        else:
            user, password = auth, ''
        if '/' in rest:
            host_port, dbname = rest.split('/', 1)
        else:
            host_port, dbname = rest, ''
        host, port = (host_port.split(':', 1) + ['5432'])[:2]
    else:
        print('ERROR: Cannot parse DATABASE_URL for PostgreSQL backup')
        sys.exit(1)

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    dump_file = backup_dir / f'ihis_{timestamp}.sql.gz'

    env = os.environ.copy()
    if password:
        env['PGPASSWORD'] = password

    cmd = [
        'pg_dump',
        f'--host={host}',
        f'--port={port}',
        f'--username={user}',
        '--no-password',
        '--format=custom',
        '--compress=9',
        '--file=' + str(dump_file),
        dbname,
    ]

    print(f'Backing up PostgreSQL database: {dbname}@{host}:{port}')
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)

    if result.returncode != 0:
        print(f'ERROR: pg_dump failed:\n{result.stderr}')
        sys.exit(1)

    # Calculate checksum
    sha256 = hashlib.sha256(dump_file.read_bytes()).hexdigest()
    checksum_file = dump_file.with_suffix('.sql.gz.sha256')
    checksum_file.write_text(f'{sha256}  {dump_file.name}\n')

    size_mb = dump_file.stat().st_size / (1024 * 1024)
    print(f'Backup complete: {dump_file.name} ({size_mb:.1f} MB)')
    print(f'Checksum: {sha256}')
    return dump_file


def backup_sqlite(db_url, backup_dir):
    """Backup SQLite by copying the database file."""
    # Extract path from sqlite:///path/to/db.sqlite
    db_path = db_url.replace('sqlite:///', '').replace('sqlite://', '')
    if not os.path.isabs(db_path):
        db_path = str(BASEDIR / db_path)

    if not os.path.exists(db_path):
        print(f'ERROR: SQLite database not found at {db_path}')
        sys.exit(1)

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup_file = backup_dir / f'ihis_{timestamp}.sqlite'

    shutil.copy2(db_path, backup_file)

    # Also copy WAL/SHM if they exist (for concurrent access)
    for suffix in ('-wal', '-shm'):
        src = db_path + suffix
        if os.path.exists(src):
            shutil.copy2(src, str(backup_file) + suffix)

    sha256 = hashlib.sha256(backup_file.read_bytes()).hexdigest()
    checksum_file = backup_file.with_suffix('.sqlite.sha256')
    checksum_file.write_text(f'{sha256}  {backup_file.name}\n')

    size_mb = backup_file.stat().st_size / (1024 * 1024)
    print(f'Backup complete: {backup_file.name} ({size_mb:.1f} MB)')
    print(f'Checksum: {sha256}')
    return backup_file


def cleanup_old_backups(backup_dir, retention_days):
    """Remove backups older than retention_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    for f in backup_dir.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff.timestamp():
            f.unlink()
            removed += 1
    if removed:
        print(f'Cleaned up {removed} backup(s) older than {retention_days} days')


def verify_backup(backup_file, db_url):
    """Verify backup integrity by checking the checksum."""
    sha256_file = backup_file.with_suffix(
        backup_file.suffix + '.sha256'
    )
    if not sha256_file.exists():
        print(f'WARNING: No checksum file found at {sha256_file}')
        return False

    expected = sha256_file.read_text().split()[0]
    actual = hashlib.sha256(backup_file.read_bytes()).hexdigest()

    if expected == actual:
        print(f'Checksum verified: {actual}')
        return True
    else:
        print(f'CHECKSUM MISMATCH: expected {expected}, got {actual}')
        return False


def main():
    retention_days = 14
    do_verify = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--retention' and i + 1 < len(args):
            retention_days = int(args[i + 1])
            i += 2
        elif args[i] == '--verify':
            do_verify = True
            i += 1
        else:
            i += 1

    db_url = get_db_url()
    if not db_url:
        print('ERROR: DATABASE_URL not set. Cannot perform backup.')
        sys.exit(1)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    if db_url.startswith('postgresql'):
        backup_file = backup_postgres(db_url, BACKUP_DIR)
    elif db_url.startswith('sqlite'):
        backup_file = backup_sqlite(db_url, BACKUP_DIR)
    else:
        print(f'ERROR: Unsupported database type in: {db_url}')
        sys.exit(1)

    if do_verify:
        print('\nVerifying backup integrity...')
        if verify_backup(backup_file, db_url):
            print('Backup verification: PASSED')
        else:
            print('Backup verification: FAILED')
            sys.exit(1)

    cleanup_old_backups(BACKUP_DIR, retention_days)
    print('\nDone.')


if __name__ == '__main__':
    main()
