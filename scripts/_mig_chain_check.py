"""Prove the Alembic migration chain applies cleanly to a truly EMPTY database
(no db.create_all() running first, as in production). Exits 0 on success."""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

DB_PATH = os.path.join(tempfile.gettempdir(), 'ihis_mig_chain.db')
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)


def main():
    os.environ['DATABASE_URL'] = 'sqlite:///' + DB_PATH.replace('\\', '/')
    os.environ['FLASK_CONFIG'] = 'development'

    from flask import Flask
    from flask_migrate import Migrate, upgrade
    from config import config_map

    # Import models FIRST so all metadata is registered.
    from app import db as _db
    import app.models  # noqa

    flask_app = Flask('migchain')
    flask_app.config.from_object(config_map['development'])
    flask_app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']

    _db.init_app(flask_app)
    migrate = Migrate(flask_app, _db)  # noqa  (binds alembic)

    with flask_app.app_context():
        # NOTE: we deliberately do NOT call db.create_all().
        print('DB path:', DB_PATH)
        upgrade()
        print('UPGRADE SUCCEEDED')
        # flask_migrate.current() only prints; read alembic_version directly.
        from sqlalchemy import text
        with _db.engine.connect() as conn:
            ver = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
        print('alembic_version:', ver)
        assert ver == '59f96da6bbf3', f'unexpected head: {ver}'
        print('HEAD OK: 59f96da6bbf3')
    print('MIGRATION CHAIN VERIFIED OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())