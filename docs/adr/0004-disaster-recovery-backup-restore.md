# 4. Disaster Recovery, Backup Packaging, and Restore Verification

Date: 2026-09-28

## Status

Accepted

## Context

The platform is deployed as a single-tenant commercial service on a single Linux VPS running PostgreSQL in Docker. Data persistence relies on a host bind-mount directory for media uploads (`./media`) and a Docker volume for database records (`postgres_data_prod`).

Prior to this decision, database backups were produced by an unversioned shell script (`~/backup_db.sh`) hosted directly on the VPS and scheduled via crontab. This presented several operational and disaster-recovery vulnerabilities:

1. **Untested Restores**: The `.sql.gz` snapshots had never undergone a verified restore drill. In disaster recovery, an untested backup is only a hypothesis; corrupted dumps, encoding mismatches, missing sequences, or incompatible extensions could prevent recovery during a real incident.
2. **Missing Media Assets**: Backups were limited strictly to PostgreSQL table rows. Teacher profile photos and uploaded assets in `./media`—irreplaceable user data that cannot be reconstructed from code or database migrations—were omitted.
3. **Lack of Version-Controlled Tooling**: The backup script was not tracked in git, making backup and restore procedures opaque to development and difficult to reproduce or test in local environments.
4. **Dangerous Overwrite Risk**: Ad-hoc restoration commands run via shell (`psql` or `pg_restore`) directly on production risk accidental data destruction if executed against the active production database without explicit targeting and confirmation safeguards.
5. **Absence of Integrity Verification**: Even when a restore command exits with code `0`, there was no automated mechanism to verify that schema migrations are valid, foreign keys are intact, password hashing works, and referenced media files exist on disk.

## Decision

We establish a formalized, version-controlled disaster recovery architecture encompassing tooling, dual-artifact packaging, safe restore mechanics, automated integrity checks, and regular non-destructive drills:

1. **Dual-Artifact Snapshot Packaging**:
   - Backups are generated as coupled, timestamped pairs sharing an identical UTC prefix (`backup_YYYYMMDD_HHMMSS`):
     - `backup_YYYYMMDD_HHMMSS_db.sql.gz`: Gzip-compressed PostgreSQL SQL dump produced via `pg_dump`.
     - `backup_YYYYMMDD_HHMMSS_media.tar.gz`: Gzip-compressed tar archive of the `./media` directory.
   - The restore tooling is capable of restoring the database alone or automatically discovering and unpacking the companion media archive.

2. **Repository-Tracked Tooling (`scripts/`)**:
   - Version-control standardized scripts in the repository:
     - `scripts/backup.sh`: Handles environment auto-detection (local vs prod), artifact packaging, and automated retention pruning of snapshots older than N days (default 7 days).
     - `scripts/restore.sh`: Executes clean, safe database and media restoration.

3. **Multi-Layer Production Overwrite Guardrails**:
   - The restore script defaults to restoring into an isolated scratch test database (e.g. `tutor_booking_restore_test`) rather than the active database.
   - Restoring to a production database requires an explicit `--dangerously-restore-to-production` flag, explicit naming of the target database, and manual interactive confirmation (`type database name to confirm`).
   - Terminate active connections prior to database drop/recreation to prevent hung or dirty restore states.

4. **Automated Integrity Verification (`verify_database_integrity`)**:
   - Provide a Django management command (`python manage.py verify_database_integrity`) that evaluates:
     - Schema & Migrations: Confirms all database migrations are applied.
     - Core Entity Metrics: Counts users (students/teachers, verified/unverified), bookings by status, reviews, and availability rules.
     - Referential Integrity: Checks for orphaned profiles or broken relational links.
     - Auth Sanity: Validates that Django's password hasher can parse and verify existing password hashes.
     - Media File Existence: Confirms files referenced in teacher avatar fields exist in `MEDIA_ROOT`.
   - Exits with `0` on success or non-zero on failure, enabling script integration.

5. **Two-Stage Verification Drill**:
   - Stage 1: Local Docker drill validating backup, drop/recreation, restore, and integrity verification in containerized development.
   - Stage 2: VPS production drill restoring the latest snapshot into an isolated scratch database (`tutor_booking_restore_test`) to verify real production backups without interrupting live traffic.

## Consequences

### Positive

- Complete recoverability of both relational data and user-uploaded media files.
- Version-controlled, reproducible backup and restore procedures executable in both local and production environments.
- Protection against accidental data loss through strict restore guardrails and test-database-by-default behavior.
- Objective, automated verification of restored database integrity across Django models, migrations, auth, and media storage.
- Clearly documented, repeatable disaster recovery runbook for scheduled drills and emergency recovery.

### Negative

- Media archiving increases disk consumption in the backup directory compared to SQL dumps alone (mitigated by automated 7-day retention pruning).
- Requires operator familiarity with running the restore script and executing non-destructive drills.
