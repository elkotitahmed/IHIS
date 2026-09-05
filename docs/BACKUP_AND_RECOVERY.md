# iHIS Backup and Recovery Guide

## Backup Strategy

### What to Back Up
1. **PostgreSQL database** — all clinical data, user accounts, audit logs
2. **Upload folder** (`var/uploads/`) — patient documents, radiology images, lab files
3. **Configuration** (`.env`) — secrets and runtime settings

### Backup Schedule
| Component | Frequency | Retention | Method |
|-----------|-----------|-----------|--------|
| Database (full) | Daily | 14 days | `pg_dump` via `backup/backup.py` |
| Database (incremental) | Every 6 hours | 3 days | WAL archiving (PostgreSQL) |
| Upload folder | Daily | 30 days | `rsync` to remote storage |
| Configuration | On change | Forever | Version control (secrets excluded) |

### Backup Commands
```bash
# Full database backup with verification
python backup/backup.py --verify

# Custom retention (keep 30 days)
python backup/backup.py --retention 30

# Upload folder backup
rsync -avz var/uploads/ backup/uploads/$(date +%Y%m%d)/

# Configuration backup (without secrets)
tar czf backup/config_$(date +%Y%m%d).tar.gz .env.example deployment/
```

## Recovery Procedures

### Database Recovery
```bash
# PostgreSQL — the backup is a pg_dump CUSTOM-format file (named *.sql.gz),
# so restore it with pg_restore (NOT psql). Create an empty target DB first:
createdb -h <host> -U <user> ihis_restore
pg_restore --no-owner --role=<user> -h <host> -U <user> -d ihis_restore \
    backup/backups/ihis_YYYYMMDD_HHMMSS.sql.gz

# SQLite (file copy)
cp backup/backups/ihis_YYYYMMDD_HHMMSS.sqlite database/ihis.db
```

### Upload Recovery
```bash
rsync -avz backup/uploads/YYYYMMDD/ var/uploads/
```

### Full System Recovery
1. Restore database from latest backup
2. Restore upload folder from latest backup
3. Run `FLASK_CONFIG=production flask db upgrade` to ensure schema is current
4. Start application
5. Verify with health checks

### Disaster Recovery Scenarios

#### Scenario A: Database Corruption
1. Stop application immediately
2. Identify last known good backup
3. Restore database
4. Run migration check: `flask db current`
5. Start application
6. Investigate root cause

#### Scenario B: Failed Migration
1. Do NOT drop the database
2. Try `flask db downgrade -1` to revert
3. If downgrade fails, restore from backup
4. Fix the migration file
5. Re-run `flask db upgrade`

#### Scenario C: Storage Failure
1. Restore upload folder from backup
2. Verify file integrity by checking download routes
3. If critical medical files lost, notify affected patients

#### Scenario D: Credential Compromise
1. Rotate SECRET_KEY immediately
2. Rotate DATABASE_PASSWORD
3. Rotate GEMINI_API_KEY if used
4. Force all users to re-login (invalidate sessions)
5. Review audit logs for unauthorized access

## Backup Security
- Backups contain PHI — restrict access to authorized personnel
- Store backups offsite or in encrypted cloud storage
- Never leave backup files in publicly accessible directories
- Set file permissions: `chmod 600 backup/backups/*`
- Test restore quarterly to verify backup integrity
