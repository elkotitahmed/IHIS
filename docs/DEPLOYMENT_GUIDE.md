# iHIS Deployment Guide

## Quick Start

### 1. Server Setup
```bash
# Install system dependencies
sudo apt update && sudo apt install -y python3.12 python3.12-venv postgresql nginx

# Create database
sudo -u postgres createdb ihis
sudo -u postgres createuser ihis_user
sudo -u postgres psql -c "ALTER USER ihis_user WITH PASSWORD 'YOUR_PASSWORD';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ihis TO ihis_user;"
```

### 2. Application Setup
```bash
cd /opt/iHIS_Project
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp deployment/.env.production.example .env
# Edit .env with real secrets

# Run migrations
FLASK_CONFIG=production flask db upgrade

# Seed roles and permissions ONLY (never demo data in production)
FLASK_CONFIG=development python seed.py --roles-only

# Start with gunicorn (Linux)
gunicorn --bind 0.0.0.0:8080 --workers 4 --timeout 120 wsgi:app

# Or with waitress (Windows)
waitress-serve --host 0.0.0.0 --port 8080 wsgi:app
```

### 3. Nginx Setup
```bash
sudo cp deployment/nginx.conf /etc/nginx/sites-available/ihis
sudo ln -s /etc/nginx/sites-available/ihis /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 4. Verify
```bash
curl http://localhost:8080/health/live    # {"status":"ok"}
curl http://localhost:8080/health/ready   # {"status":"ok","database":"connected"}
```

## Deployment Checklist

- [ ] SECRET_KEY set (64-char hex minimum)
- [ ] DATABASE_URL set (PostgreSQL for production)
- [ ] `flask db upgrade` completed under FLASK_CONFIG=production
- [ ] Roles seeded with `seed.py --roles-only`
- [ ] Nginx configured with TLS
- [ ] Gunicorn/waitress running on port 8080
- [ ] Health checks returning 200
- [ ] Backup schedule configured
- [ ] Monitoring configured

## Rollback Procedure

1. Stop the application
2. Restore database from backup:
   ```bash
   # PostgreSQL
   pg_restore -d ihis backup/backups/ihis_YYYYMMDD_HHMMSS.sql.gz
   # SQLite
   cp backup/backups/ihis_YYYYMMDD_HHMMSS.sqlite database/ihis.db
   ```
3. If migration rollback needed:
   ```bash
   FLASK_CONFIG=production flask db downgrade -1
   ```
4. Restart application

## Zero-Downtime Deployment

For upgrades without downtime:
1. Backup database
2. Run `flask db upgrade` (migrations should be backward-compatible)
3. Deploy new code
4. Restart gunicorn workers: `kill -HUP $(cat gunicorn.pid)`
5. Verify health checks
6. If issues: rollback code + restore database
