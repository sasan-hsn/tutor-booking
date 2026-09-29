#!/usr/bin/env bash
#
# scripts/backup.sh - Dual-artifact backup script for PostgreSQL and media assets.
#
# Creates coupled, timestamped backups:
#   backup_<TIMESTAMP>_db.sql.gz
#   backup_<TIMESTAMP>_media.tar.gz
#
# Features:
#   - Environment auto-detection (prod vs local from Compose / .env files)
#   - Explicit --env prod|local override
#   - Dump integrity verification with gzip -t
#   - Paired media archiving with integrity check
#   - Automatic pruning of snapshots older than retention threshold (default 7 days)
#   - Atomic error cleanup (removes incomplete files on failure)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Configuration defaults
TARGET_ENV=""
BACKUP_DIR_EXPLICIT=""
BACKUP_DIR="${BACKUP_DIR:-}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
SKIP_MEDIA="${SKIP_MEDIA:-false}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
BACKUP_SUCCESS=false

DB_BACKUP_FILE=""
MEDIA_BACKUP_FILE=""

show_help() {
    cat << 'EOF'
Usage: scripts/backup.sh [OPTIONS]

Dual-artifact backup script for PostgreSQL database and uploaded media assets.
Generates coupled timestamped archives:
  backup_<YYYYMMDD_HHMMSS>_db.sql.gz
  backup_<YYYYMMDD_HHMMSS>_media.tar.gz

Options:
  -e, --env <prod|local>       Environment profile (default: auto-detected)
  -b, --backup-dir <path>      Destination directory for backups (default: ./backups or ~/backups)
  -r, --retention-days <days>  Days to retain backup archives (default: 7, 0 to disable)
      --skip-media             Skip media asset archiving (database only)
  -h, --help                   Display this help message and exit

Environment Variables (overrides):
  CONTAINER_NAME               Docker container name
  POSTGRES_DB                  Target database name
  POSTGRES_USER                PostgreSQL user
  MEDIA_DIR                    Media directory path
  BACKUP_DIR                   Backup destination directory
  RETENTION_DAYS               Retention window in days
  DOCKER_BIN                   Docker binary path or command (default: docker)
EOF
}

cleanup() {
    local exit_code=$?
    if [[ "$BACKUP_SUCCESS" != "true" && $exit_code -ne 0 ]]; then
        echo "" >&2
        echo "[ERROR] Backup failed or was interrupted (code: ${exit_code}). Cleaning up incomplete artifacts..." >&2
        if [[ -n "${DB_BACKUP_FILE:-}" ]]; then
            rm -f "${DB_BACKUP_FILE}"
        fi
        if [[ -n "${MEDIA_BACKUP_FILE:-}" ]]; then
            rm -f "${MEDIA_BACKUP_FILE}"
        fi
    fi
}
trap 'exit 130' INT
trap 'exit 143' TERM
trap cleanup EXIT

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -e|--env)
            if [[ -z "${2:-}" || "${2:0:1}" == "-" ]]; then
                echo "[ERROR] Option '$1' requires an argument ('prod' or 'local')." >&2
                exit 1
            fi
            TARGET_ENV="$2"
            shift 2
            ;;
        --env=*)
            TARGET_ENV="${1#*=}"
            shift 1
            ;;
        -b|--backup-dir)
            if [[ -z "${2:-}" || "${2:0:1}" == "-" ]]; then
                echo "[ERROR] Option '$1' requires a directory path." >&2
                exit 1
            fi
            BACKUP_DIR_EXPLICIT="$2"
            shift 2
            ;;
        --backup-dir=*)
            BACKUP_DIR_EXPLICIT="${1#*=}"
            shift 1
            ;;
        -r|--retention-days)
            if [[ -z "${2:-}" || "${2:0:1}" == "-" ]]; then
                echo "[ERROR] Option '$1' requires a number of days." >&2
                exit 1
            fi
            RETENTION_DAYS="$2"
            shift 2
            ;;
        --retention-days=*)
            RETENTION_DAYS="${1#*=}"
            shift 1
            ;;
        --skip-media)
            SKIP_MEDIA=true
            shift 1
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "[ERROR] Unknown option: $1" >&2
            echo "Run 'scripts/backup.sh --help' for usage." >&2
            exit 1
            ;;
    esac
done

# Validate retention days
if ! [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
    echo "[ERROR] Retention days must be a non-negative integer. Got: '${RETENTION_DAYS}'" >&2
    exit 1
fi

# Helper function to extract env var value from file
extract_env() {
    local key="$1"
    local file="$2"
    if [[ -f "$file" ]]; then
        (grep -E "^[[:space:]]*${key}=" "$file" 2>/dev/null || true) | tail -n 1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//'
    fi
}

# Environment auto-detection if not explicitly passed
ENV_SOURCE="auto-detected"
if [[ -z "$TARGET_ENV" ]]; then
    if [[ -f "$PROJECT_ROOT/.env.prod" ]]; then
        TARGET_ENV="prod"
    elif command -v "$DOCKER_BIN" &>/dev/null && "$DOCKER_BIN" ps --format '{{.Names}}' 2>/dev/null | grep -Exq "tutor_booking_db_prod"; then
        TARGET_ENV="prod"
    else
        TARGET_ENV="local"
    fi
else
    ENV_SOURCE="explicit"
fi

# Validate TARGET_ENV
if [[ "$TARGET_ENV" != "prod" && "$TARGET_ENV" != "local" ]]; then
    echo "[ERROR] Invalid environment '${TARGET_ENV}'. Must be 'prod' or 'local'." >&2
    exit 1
fi

# Resolve environment defaults and extract overrides from env files
if [[ "$TARGET_ENV" == "prod" ]]; then
    CONTAINER_NAME="${CONTAINER_NAME:-tutor_booking_db_prod}"
    ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env.prod}"
    DEFAULT_DB="tutor_booking_prod"
    DEFAULT_USER="tutor_user"
    DEFAULT_BACKUP_DIR="${HOME}/backups"
else
    CONTAINER_NAME="${CONTAINER_NAME:-tutor_booking_db}"
    ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"
    DEFAULT_DB="tutor_booking_db"
    DEFAULT_USER="postgres"
    DEFAULT_BACKUP_DIR="$PROJECT_ROOT/backups"
fi

# Resolve backup destination directory (defaults to ./backups or ~/backups)
if [[ -n "$BACKUP_DIR_EXPLICIT" ]]; then
    BACKUP_DIR="$BACKUP_DIR_EXPLICIT"
elif [[ -z "$BACKUP_DIR" ]]; then
    BACKUP_DIR="$DEFAULT_BACKUP_DIR"
fi

# Expand tilde in backup dir if needed
if [[ "$BACKUP_DIR" == "~"* ]]; then
    BACKUP_DIR="${HOME}${BACKUP_DIR:1}"
fi

# Normalize backup directory to absolute path
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"

if [[ -f "$ENV_FILE" ]]; then
    extracted_db=$(extract_env "POSTGRES_DB" "$ENV_FILE")
    extracted_user=$(extract_env "POSTGRES_USER" "$ENV_FILE")
    if [[ -n "$extracted_db" && -z "${POSTGRES_DB:-}" ]]; then
        POSTGRES_DB="$extracted_db"
    fi
    if [[ -n "$extracted_user" && -z "${POSTGRES_USER:-}" ]]; then
        POSTGRES_USER="$extracted_user"
    fi
fi

POSTGRES_DB="${POSTGRES_DB:-$DEFAULT_DB}"
POSTGRES_USER="${POSTGRES_USER:-$DEFAULT_USER}"
MEDIA_DIR="${MEDIA_DIR:-$PROJECT_ROOT/media}"

# Normalize media directory to absolute path if it exists
if [[ -d "$MEDIA_DIR" ]]; then
    MEDIA_DIR="$(cd "$MEDIA_DIR" && pwd)"
fi

echo "=================================================="
echo "Starting Backup"
echo "  Environment:   ${TARGET_ENV} (${ENV_SOURCE})"
echo "  Container:     ${CONTAINER_NAME}"
echo "  Database:      ${POSTGRES_DB} (user: ${POSTGRES_USER})"
echo "  Media Dir:     ${MEDIA_DIR}"
echo "  Backup Dir:    ${BACKUP_DIR}"
echo "  Retention:     ${RETENTION_DAYS} days"
echo "  Skip Media:    ${SKIP_MEDIA}"
echo "=================================================="

# Pre-flight checks
if ! command -v "$DOCKER_BIN" &>/dev/null; then
    echo "[ERROR] 'docker' command is required but not installed or not in PATH." >&2
    exit 1
fi

if ! command -v gzip &>/dev/null; then
    echo "[ERROR] 'gzip' command is required but not installed or not in PATH." >&2
    exit 1
fi

if ! command -v tar &>/dev/null; then
    echo "[ERROR] 'tar' command is required but not installed or not in PATH." >&2
    exit 1
fi

running_containers=$("$DOCKER_BIN" ps --format '{{.Names}}' 2>/dev/null || true)
if ! echo "$running_containers" | grep -Exq "${CONTAINER_NAME}"; then
    echo "[ERROR] Database container '${CONTAINER_NAME}' is not running." >&2
    exit 1
fi

# Pre-flight media check: if media is not skipped, media dir must exist
if [[ "$SKIP_MEDIA" != "true" && ! -d "$MEDIA_DIR" ]]; then
    echo "[ERROR] Media directory '${MEDIA_DIR}' does not exist. Use --skip-media for database-only backups." >&2
    exit 1
fi

# Generate UTC timestamp
TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
DB_BACKUP_FILE="${BACKUP_DIR}/backup_${TIMESTAMP}_db.sql.gz"
MEDIA_BACKUP_FILE="${BACKUP_DIR}/backup_${TIMESTAMP}_media.tar.gz"

# Helper for gzip integrity verification
verify_gzip() {
    local file="$1"
    local desc="$2"
    echo "      Verifying ${desc} integrity with gzip -t..."
    if ! gzip -t "$file" 2>/dev/null; then
        echo "[ERROR] ${desc} failed gzip integrity check: ${file}" >&2
        rm -f "$file"
        exit 1
    fi
}

# 1. Database dump
echo "[1/3] Dumping PostgreSQL database '${POSTGRES_DB}'..."
"$DOCKER_BIN" exec "$CONTAINER_NAME" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip -c > "$DB_BACKUP_FILE"

if [[ ! -s "$DB_BACKUP_FILE" ]]; then
    echo "[ERROR] Database backup file is empty or was not generated: ${DB_BACKUP_FILE}" >&2
    rm -f "$DB_BACKUP_FILE"
    exit 1
fi

verify_gzip "$DB_BACKUP_FILE" "database backup"

uncompressed_db_bytes=$( (gzip -l "$DB_BACKUP_FILE" 2>/dev/null || true) | awk 'NR==2 {print $2}')
if [[ "${uncompressed_db_bytes:-0}" -le 0 ]]; then
    echo "[ERROR] Database backup contains 0 uncompressed bytes: ${DB_BACKUP_FILE}" >&2
    rm -f "$DB_BACKUP_FILE"
    exit 1
fi
db_size=$(du -h "$DB_BACKUP_FILE" | awk '{print $1}')
echo "      [OK] Database backup verified (${db_size}): ${DB_BACKUP_FILE}"

# 2. Media archiving
if [[ "$SKIP_MEDIA" == "true" ]]; then
    echo "[2/3] Skipping media archiving (--skip-media specified)."
    MEDIA_BACKUP_FILE=""
else
    echo "[2/3] Archiving media directory '${MEDIA_DIR}'..."
    media_parent="$(dirname "$MEDIA_DIR")"
    media_base="$(basename "$MEDIA_DIR")"
    tar -czf "$MEDIA_BACKUP_FILE" -C "$media_parent" "$media_base"

    if [[ ! -s "$MEDIA_BACKUP_FILE" ]]; then
        echo "[ERROR] Media backup file is empty: ${MEDIA_BACKUP_FILE}" >&2
        rm -f "$MEDIA_BACKUP_FILE"
        exit 1
    fi

    verify_gzip "$MEDIA_BACKUP_FILE" "media archive"

    if ! tar -tzf "$MEDIA_BACKUP_FILE" &>/dev/null; then
        echo "[ERROR] Media archive failed tar content listing verification: ${MEDIA_BACKUP_FILE}" >&2
        rm -f "$MEDIA_BACKUP_FILE"
        exit 1
    fi
    media_size=$(du -h "$MEDIA_BACKUP_FILE" | awk '{print $1}')
    echo "      [OK] Media archive verified (${media_size}): ${MEDIA_BACKUP_FILE}"
fi

# 3. Retention pruning
echo "[3/3] Evaluating backup retention policy..."
if [[ "$RETENTION_DAYS" -le 0 ]]; then
    echo "      Retention pruning disabled (retention-days <= 0)."
else
    pruned_count=0
    pruning_minutes=$((RETENTION_DAYS * 1440))
    while IFS= read -r -d '' old_file; do
        echo "      [PRUNE] Removing expired snapshot: $(basename "$old_file")"
        rm -f "$old_file"
        pruned_count=$((pruned_count + 1))
    done < <(find "$BACKUP_DIR" -maxdepth 1 -type f \( -name "backup_*_db.sql.gz" -o -name "backup_*_media.tar.gz" \) -mmin +"$pruning_minutes" -print0)

    if [[ $pruned_count -gt 0 ]]; then
        echo "      [OK] Pruned ${pruned_count} artifact(s) older than ${RETENTION_DAYS} days."
    else
        echo "      [OK] 0 artifacts older than ${RETENTION_DAYS} days."
    fi
fi

BACKUP_SUCCESS=true

echo "=================================================="
echo "Backup Completed Successfully"
echo "  Environment:    ${TARGET_ENV} (${ENV_SOURCE})"
echo "  Database Dump:  ${DB_BACKUP_FILE} (${db_size})"
if [[ -n "${MEDIA_BACKUP_FILE:-}" && -f "${MEDIA_BACKUP_FILE}" ]]; then
    echo "  Media Archive:  ${MEDIA_BACKUP_FILE} (${media_size})"
else
    echo "  Media Archive:  Skipped"
fi
echo "  Backup Dir:     ${BACKUP_DIR}"
echo "  Retention:      ${RETENTION_DAYS} days"
echo "=================================================="

exit 0
