# Disaster Recovery Runbook: Backup, Restore & Verification

This runbook provides step-by-step instructions for operating, verifying, and recovering the `englishwithmary.ir` platform in both local development and production VPS environments.

## Overview

The platform uses dual-artifact timestamped backups produced by `scripts/backup.sh`:
- Database snapshot: `backup_<YYYYMMDD_HHMMSS>_db.sql.gz`
- Media archive: `backup_<YYYYMMDD_HHMMSS>_media.tar.gz`

Restoration is handled by `scripts/restore.sh`, which defaults safely to an isolated test scratch database (`tutor_booking_restore_test`) and actively prevents accidental overwrites of the live production database.

---

## 1. Automated Daily Backups (Production Cron)

On the production VPS, backups run daily via crontab under the `deployer` user.

### Production Crontab Setup

Ensure the backup destination directory exists:

```bash
mkdir -p /home/deployer/backups
chmod 700 /home/deployer/backups
```

Open crontab:

```bash
crontab -e
```

Configure the daily backup job (runs at 02:00 UTC with 7-day retention):

```cron
0 2 * * * /home/deployer/app/scripts/backup.sh --env prod --backup-dir /home/deployer/backups --retention-days 7 >> /home/deployer/backups/backup.log 2>&1
```

### Manual On-Demand Backup

To create an immediate backup before deployments or maintenance:

```bash
cd /home/deployer/app
./scripts/backup.sh --env prod
```

---

## 2. Local Docker Disaster Recovery Drill (Development)

Developers can execute a complete, non-destructive disaster recovery verification drill locally using Docker Compose.

### Step 1: Generate Local Test Backup

```bash
cd /path/to/tutor-booking
./scripts/backup.sh --env local --backup-dir ./backups
```

### Step 2: Restore Snapshot to Isolated Scratch Database

Restore the generated backup into `tutor_booking_restore_test` and unpack media to a temporary drill directory:

```bash
LATEST_LOCAL_DB=$(ls -t ./backups/backup_*_db.sql.gz | head -n 1)
./scripts/restore.sh --db-file "$LATEST_LOCAL_DB" --env local --target-media-dir /tmp/local_drill_media
```

### Step 3: Run Database & Media Integrity Verification

Run the verification management command pointed at the scratch database and restored media directory:

```bash
DATABASE_URL="postgres://postgres:postgrespassword@localhost:6543/tutor_booking_restore_test" \
MEDIA_ROOT="/tmp/local_drill_media" \
python manage.py verify_database_integrity
```

Expected output:
```text
==================================================
Verification Summary: PASSED (0 errors, 0 warning(s))
==================================================
```

### Step 4: Clean Up Local Drill Artifacts

```bash
docker compose exec postgres psql -U postgres -d postgres -c "DROP DATABASE IF EXISTS tutor_booking_restore_test;"
rm -rf /tmp/local_drill_media
```

---

## 3. Routine Non-Destructive Verification Drill (Production VPS)

Routine verification drills ensure production backup files are valid and recoverable **without interrupting live website traffic**. The restore script restores the latest backup into an isolated scratch database `tutor_booking_restore_test`, followed by running Django's integrity verification command.

### Step 1: Identify the Latest Backup Snapshot

```bash
cd /home/deployer/app
LATEST_PROD_DB=$(ls -t /home/deployer/backups/backup_*_db.sql.gz | head -n 1)
echo "Testing snapshot: $LATEST_PROD_DB"
```

### Step 2: Restore Snapshot to Isolated Scratch Database

The script defaults to `--target-db tutor_booking_restore_test` and automatically discovers the companion media archive:

```bash
./scripts/restore.sh --db-file "$LATEST_PROD_DB" --env prod --target-media-dir /tmp/drill_media
```

### Step 3: Verify Database and Media Integrity

Execute Django's database integrity verification command against the restored scratch database and restored media files:

```bash
# Extract production database URL and replace database name with scratch database
DRILL_DB_URL=$(grep -E '^[[:space:]]*DATABASE_URL=' .env.prod | cut -d= -f2- | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//' -e 's/\/tutor_booking_prod$/\/tutor_booking_restore_test/')

docker compose -f docker-compose.prod.yml run --rm \
  -e DATABASE_URL="$DRILL_DB_URL" \
  -v /tmp/drill_media:/app/drill_media:ro \
  -e MEDIA_ROOT="/app/drill_media" \
  web python manage.py verify_database_integrity
```

Expected output:
```text
==================================================
Verification Summary: PASSED (0 errors, 0 warning(s))
==================================================
```

### Step 4: Clean Up Drill Scratch Resources

Drop the scratch test database and temporary media folder:

```bash
docker compose -f docker-compose.prod.yml exec postgres psql -U tutor_user -d postgres -c "DROP DATABASE IF EXISTS tutor_booking_restore_test;"
rm -rf /tmp/drill_media
```

---

## 4. Emergency Full Recovery (Live Production Restore)

Use this procedure only during an actual disaster recovery event (e.g. data corruption, catastrophic database failure).

> **WARNING:** This procedure completely drops and replaces the active production database.

### Step 1: Put Application Services in Maintenance Mode

Stop the web and Celery services to prevent incoming HTTP traffic and background worker database writes during restore:

```bash
cd /home/deployer/app
docker compose -f docker-compose.prod.yml stop web celery_worker celery_beat
```

### Step 2: Set Target Backup File and Execute Live Restore

Assign the desired backup file path:

```bash
TARGET_DB_FILE="/home/deployer/backups/backup_YYYYMMDD_HHMMSS_db.sql.gz"
```

Target the live production database (`tutor_booking_prod`) using the mandatory safety flag:

```bash
./scripts/restore.sh \
  --db-file "$TARGET_DB_FILE" \
  --env prod \
  --target-db tutor_booking_prod \
  --dangerously-restore-to-production
```

When prompted:
```text
Type 'tutor_booking_prod' to confirm destruction and restoration:
```
Type `tutor_booking_prod` and press Enter to proceed.

### Step 3: Run Post-Restore Database Verification

```bash
docker compose -f docker-compose.prod.yml run --rm web python manage.py verify_database_integrity
```

### Step 4: Restart Application Services

```bash
docker compose -f docker-compose.prod.yml up -d
```

Verify service health:
```bash
docker compose -f docker-compose.prod.yml ps
curl -I https://englishwithmary.ir/health/
```

---

## 5. Troubleshooting Common Failures

### Target Database Locked / Active Connections
- `scripts/restore.sh` automatically executes `pg_terminate_backend()` against active connections before dropping the target database.
- If connections persistently re-open, ensure `web`, `celery_worker`, and `celery_beat` are stopped:
  ```bash
  docker compose -f docker-compose.prod.yml stop web celery_worker celery_beat
  ```

### Host Permissions on Media Directory
- Ensure bind mount directory permissions on VPS host allow write access for `deployer` and traversal for Nginx (`www-data`):
  ```bash
  chmod o+x /home/deployer /home/deployer/app
  ```

### Corrupted Snapshot (gzip -t failure)
- If `scripts/restore.sh` reports `[ERROR] Database snapshot failed gzip integrity check`, the snapshot file is damaged. Do not attempt to force a restore.
- Select the preceding valid timestamped snapshot from `/home/deployer/backups/`.
