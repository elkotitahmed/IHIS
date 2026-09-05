"""Restore-verify a SQLite iHIS backup: open it as a fresh DB, confirm the
expected tables exist and alembic_version is present (Phase 22)."""
import sqlite3
import sys

con = sqlite3.connect(r'D:\AI in health care\protoproject\iHIS_Project\database\_restore_test.db')
try:
    c = con.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tabs = [r[0] for r in c.fetchall()]
    c.execute('SELECT version_num FROM alembic_version')
    ver = c.fetchone()[0]
    print('RESTORED tables:', len(tabs))
    print('alembic_version:', ver)
    assert ver == '59f96da6bbf3'
    assert 'patients' in tabs and 'users' in tabs and 'medical_records' in tabs
    print('RESTORE VERIFICATION OK')
finally:
    con.close()