# Feature Specification: Disaster Recovery, Backup Packaging, and Restore Verification

## Problem Statement

The platform is deployed as a single-instance commercial service on a single Linux VPS. Relational data lives in PostgreSQL (`postgres_data_prod` Docker volume) and user-uploaded media files (teacher profile photos) live in a bind-mounted host directory (`./media`).

Prior to this work, database backups were run via a host-only crontab script (`~/backup_db.sh`) dumping compressed SQL to the host disk with 7-day retention. This setup had critical shortcomings:
1. **Untested Restores**: The generated `.sql.gz` files had never been restored or verified in a drill. An untested backup provides false security; syntax incompatibilities, corrupt gzip streams, sequence errors, or missing extensions could render backups unusable during a real outage.
2. **Missing Media Files**: Uploaded teacher avatars and assets were omitted from the backup cycle.
3. **Unversioned Scripts**: The backup logic was not part of the git repository, preventing local testing, reproducible deployments, and peer review.
4. **Safety Risk**: There were no safety guardrails preventing an operator from accidentally wiping the active production database during a manual restore.
5. **No Automated Integrity Check**: There was no standard mechanism to verify that a restored database works seamlessly with Django's ORM, auth hashes, migrations, and media storage.

## Solution Overview

Build and verify an end-to-end disaster recovery subsystem:
1. **Dual-Artifact Backup (`scripts/backup.sh`)**:
   - Creates matching timestamped pairs: `backup_<TIMESTAMP>_db.sql.gz` and `backup_<TIMESTAMP>_media.tar.gz`.
   - Supports local development containers (`tutor_booking_db`) and production containers (`tutor_booking_db_prod`).
   - Automatically prunes archives older than retention threshold (default 7 days) upon successful backup.
2. **Safe Restore Tool (`scripts/restore.sh`)**:
   - Safely restores `.sql.gz` and companion `.tar.gz` into target database and media folders.
   - Defaults to restoring into an isolated scratch test database (`tutor_booking_restore_test`) for safe non-destructive verification drills.
   - Enforces strict guardrails (explicit flags, target verification, interactive confirmation) before allowing any restore against production databases.
3. **Django Integrity Verification Command (`verify_database_integrity`)**:
   - Management command `python manage.py verify_database_integrity` validating:
     - Unapplied migrations check.
     - Entity counts across all core models.
     - Referential integrity and absence of orphaned profiles.
     - Password hashing engine sanity.
     - Media file link validity against files on disk.
   - Emits structured terminal output and exits with code `0` on success or `1` on failure.
4. **Disaster Recovery Runbook (`docs/runbooks/disaster-recovery.md`)**:
   - Step-by-step operational guide for:
     - Routine disaster recovery drills (non-destructive, against scratch DB).
     - Full production recovery procedures.
5. **Two-Stage Verification**:
   - Verification drill executed in local Docker environment.
   - Operator runbook for executing the drill on the VPS production host.

## User & Operator Stories

1. As a system operator, I want automated backups to bundle both database rows and uploaded teacher media files, so that user profiles and assets can be fully restored together.
2. As a system operator, I want backup and restore scripts checked into the git repository, so that recovery procedures are versioned, documented, and testable locally.
3. As a developer, I want to run disaster recovery drills into an isolated scratch database without risking or interrupting the live production service.
4. As a system operator, I want the restore script to prevent accidental overwrites of the production database unless an explicit danger flag and typed confirmation are provided.
5. As a developer or operator, I want an automated command to verify database integrity after a restore, so that I have immediate proof that Django models, migrations, auth hashes, and media files are sound.
6. As a system operator, I want backups older than 7 days to be automatically pruned after a successful backup, so that VPS disk space is conserved.

## Implementation Details

### 1. `scripts/backup.sh`

- **Purpose**: Create timestamped PostgreSQL snapshot and media archive, followed by retention pruning.
- **CLI Options**:
  - `--env [prod|local]`: Environment profile (defaults to auto-detecting via existence of `.env.prod` / `docker-compose.prod.yml`).
  - `--backup-dir <path>`: Destination directory for backups (default: `./backups` or `~/backups` on VPS).
  - `--skip-media`: Skip media archive creation (database-only backup).
  - `--retention-days <N>`: Days of backups to keep (default: 7).
- **Execution Flow**:
  1. Determine container name, DB name, DB user, and media source path based on environment.
  2. Generate UTC timestamp string `YYYYMMDD_HHMMSS`.
  3. Ensure backup destination directory exists with secure permissions (`700`).
  4. Dump database using `docker exec <container> pg_dump -U <user> <dbname> | gzip -c > <dest>/backup_<TIMESTAMP>_db.sql.gz`.
  5. Validate that `.sql.gz` is non-empty and uncorrupted using `gzip -t`.
  6. If not `--skip-media` and media directory exists, archive using `tar -czf <dest>/backup_<TIMESTAMP>_media.tar.gz -C <parent_dir> media`.
  7. If backup succeeded, prune files matching `backup_*_db.sql.gz` and `backup_*_media.tar.gz` older than `--retention-days`.

### 2. `scripts/restore.sh`

- **Purpose**: Safely restore database and media from a backup snapshot.
- **CLI Options**:
  - `--db-file <path>`: Path to `.sql.gz` snapshot (required).
  - `--media-file <path>`: Path to `.tar.gz` media archive (optional; defaults to companion file if present).
  - `--target-db <name>`: Target database name (default: `tutor_booking_restore_test`).
  - `--env [prod|local]`: Environment profile (defaults to auto-detect).
  - `--skip-media`: Skip restoring media files.
  - `--dangerously-restore-to-production`: Required if `--target-db` matches the live production database name.
- **Safety Guardrails**:
  - If `--target-db` matches the production database (e.g. `tutor_booking_db` in prod config), fail immediately unless `--dangerously-restore-to-production` is supplied.
  - If `--dangerously-restore-to-production` is supplied, require interactive typed confirmation of the database name.
- **Execution Flow**:
  1. Verify backup file exists and passes `gzip -t`.
  2. Terminate existing connections to `--target-db`:
     `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '<target-db>' AND pid <> pg_backend_pid();`
  3. Drop and recreate `--target-db`:
     `DROP DATABASE IF EXISTS <target-db>; CREATE DATABASE <target-db>;`
  4. Restore dump:
     `gunzip -c <db-file> | docker exec -i <container> psql -U <user> -d <target-db>`
  5. If media restore is active:
     Unpack media archive to designated target media directory.

### 3. Management Command: `verify_database_integrity`

- **Location**: `accounts/management/commands/verify_database_integrity.py` (or under `accounts/` / `booking/`).
- **Options**:
  - `--database <db_alias>`: Django database connection to inspect (default: `'default'`).
  - `--check-media`: Verify that avatar/photo paths in database exist in `MEDIA_ROOT` (default: True).
  - `--sample-auth`: Test password hasher validity against a sample of user records (default: True).
- **Checks Performed**:
  1. **Migrations**: Check that no unapplied migrations exist via `MigrationExecutor.migration_plan()`.
  2. **Counts & Status Summary**:
     - Users total (students, teachers, active, verified vs unverified).
     - Bookings total (pending, confirmed, completed, cancelled, expired).
     - TeacherProfiles, StudentProfiles, Reviews, RegularAvailability, WeeklyOverride.
  3. **Referential Integrity**:
     - Ensure all `TeacherProfile` and `StudentProfile` point to valid `User` records.
     - Ensure all `Booking` records have valid `student` and `teacher` foreign keys.
     - Ensure all `Review` records link to valid `Booking`, `student`, and `teacher`.
  4. **Auth Sanity**:
     - Inspect user password hashes; ensure `identify_hasher` succeeds and format matches supported Django hashers (PBKDF2/Argon2/bcrypt).
  5. **Media Cross-Check**:
     - Scan `TeacherProfile.avatar` / `photo` fields; check that referenced files exist in `default_storage`.
- **Output & Exit Codes**:
  - Formatted terminal report with summary statistics and pass/fail indicators.
  - Return exit code `0` on 100% pass; return exit code `1` if any migration is unapplied, relational integrity error detected, or hasher failure found.

### 4. Disaster Recovery Runbook (`docs/runbooks/disaster-recovery.md`)

- Structured operational guide with explicit copy-pasteable commands for:
  - Performing routine non-destructive drills on production VPS.
  - Emergency full-system recovery from backup.
  - Troubleshooting common restore failures (permissions, locks, missing files).

## Testing & Quality Plan

1. **Unit & Integration Tests**:
   - `accounts/tests/test_verify_database_integrity.py`:
     - Test clean database passes all checks with code 0.
     - Test missing media file produces warning or failure as configured.
     - Test unapplied migration detection.
     - Test referential integrity reporting on corrupted relations.
2. **Local End-to-End Drill**:
   - Generate test backup with `scripts/backup.sh`.
   - Restore to isolated database `tutor_booking_restore_test` with `scripts/restore.sh`.
   - Run `python manage.py verify_database_integrity --database=restore_test` (or against test DB container).
   - Validate exit code 0 and complete data retention.
