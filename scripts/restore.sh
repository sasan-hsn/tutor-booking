#!/usr/bin/env bash
#
# scripts/restore.sh - Disaster recovery restore tooling for database and media assets.
#
# Safely restores PostgreSQL database snapshots and optional companion media archives.
#
# Features:
#   - Safe-by-default: defaults target database to 'tutor_booking_restore_test'
#   - Multi-layer production safeguards: actively blocks restore to live production DB
#     unless --dangerously-restore-to-production and exact typed confirmation are supplied
#   - Pre-flight backup file existence and gzip stream verification (gzip -t) before dropping DB
#   - Clean active connection termination and atomic DROP DATABASE / CREATE DATABASE
#   - Clean uncompressed SQL dump streaming into target container via psql
#   - Automatic companion media discovery and unpacking with --skip-media override
#   - Explicit environment profile selection (--env prod|local) or automatic detection
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Configuration defaults
DB_FILE=""
MEDIA_FILE=""
MEDIA_FILE_EXPLICIT=""
TARGET_DB=""
DEFAULT_TARGET_DB="tutor_booking_restore_test"
TARGET_ENV=""
SKIP_MEDIA=false
TARGET_MEDIA_DIR=""
DANGEROUSLY_RESTORE_TO_PROD=false
DOCKER_BIN="${DOCKER_BIN:-docker}"
MAINTENANCE_DB="${MAINTENANCE_DB:-postgres}"

trap 'exit 130' INT
trap 'exit 143' TERM

show_help() {
    cat << 'EOF'
Usage: scripts/restore.sh --db-file <path> [OPTIONS]

Disaster recovery restore script for PostgreSQL database and companion media archives.
Defaults safely to isolated test scratch database 'tutor_booking_restore_test'.

Options:
  -d, --db-file <path>                  Path to database snapshot (.sql.gz) [REQUIRED]
  -m, --media-file <path>               Path to media archive (.tar.gz) (default: companion file)
  -t, --target-db <name>                Target database name (default: tutor_booking_restore_test)
  -e, --env <prod|local>                Environment profile (default: auto-detected)
      --target-media-dir <path>         Destination directory for media files (default: ./media)
      --skip-media                      Skip media asset restoration
      --dangerously-restore-to-production
                                        Allow restore to live production database (requires typed confirmation)
  -h, --help                            Display this help message and exit

Environment Variables (overrides):
  CONTAINER_NAME                        Docker container name
  POSTGRES_USER                         PostgreSQL user
  POSTGRES_DB                           Default database name for environment
  MEDIA_DIR                             Default media destination directory
  DOCKER_BIN                            Docker binary command (default: docker)
  MAINTENANCE_DB                        Maintenance database for DROP/CREATE (default: postgres)
EOF
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -d|--db-file)
            if [[ -z "${2:-}" || "${2:0:1}" == "-" ]]; then
                echo "[ERROR] Option '$1' requires a file path." >&2
                exit 1
            fi
            DB_FILE="$2"
            shift 2
            ;;
        --db-file=*)
            DB_FILE="${1#*=}"
            shift 1
            ;;
        -m|--media-file)
            if [[ -z "${2:-}" || "${2:0:1}" == "-" ]]; then
                echo "[ERROR] Option '$1' requires a file path." >&2
                exit 1
            fi
            MEDIA_FILE_EXPLICIT="$2"
            shift 2
            ;;
        --media-file=*)
            MEDIA_FILE_EXPLICIT="${1#*=}"
            shift 1
            ;;
        -t|--target-db)
            if [[ -z "${2:-}" || "${2:0:1}" == "-" ]]; then
                echo "[ERROR] Option '$1' requires a database name." >&2
                exit 1
            fi
            TARGET_DB="$2"
            shift 2
            ;;
        --target-db=*)
            TARGET_DB="${1#*=}"
            shift 1
            ;;
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
        --target-media-dir)
            if [[ -z "${2:-}" || "${2:0:1}" == "-" ]]; then
                echo "[ERROR] Option '$1' requires a directory path." >&2
                exit 1
            fi
            TARGET_MEDIA_DIR="$2"
            shift 2
            ;;
        --target-media-dir=*)
            TARGET_MEDIA_DIR="${1#*=}"
            shift 1
            ;;
        --skip-media)
            SKIP_MEDIA=true
            shift 1
            ;;
        --dangerously-restore-to-production)
            DANGEROUSLY_RESTORE_TO_PROD=true
            shift 1
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "[ERROR] Unknown option: $1" >&2
            echo "Run 'scripts/restore.sh --help' for usage." >&2
            exit 1
            ;;
    esac
done

# Helper function to expand tilde in paths
expand_tilde() {
    local path="$1"
    if [[ "$path" == "~"* ]]; then
        echo "${HOME}${path:1}"
    else
        echo "$path"
    fi
}

# Validate required --db-file
if [[ -z "$DB_FILE" ]]; then
    echo "[ERROR] Option --db-file is required." >&2
    echo "Run 'scripts/restore.sh --help' for usage." >&2
    exit 1
fi

DB_FILE="$(expand_tilde "$DB_FILE")"

# Check DB_FILE existence and non-empty
if [[ ! -s "$DB_FILE" ]]; then
    echo "[ERROR] Database backup file does not exist or is empty: ${DB_FILE}" >&2
    exit 1
fi

# Verify gzip stream integrity before proceeding
echo "==> Verifying database snapshot integrity with gzip -t..."
if ! gzip -t "$DB_FILE" 2>/dev/null; then
    echo "[ERROR] Database snapshot failed gzip integrity check: ${DB_FILE}" >&2
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
else
    CONTAINER_NAME="${CONTAINER_NAME:-tutor_booking_db}"
    ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"
    DEFAULT_DB="tutor_booking_db"
    DEFAULT_USER="postgres"
fi

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

# Determine production database identifier for safety guardrails
PROD_DB_NAME="tutor_booking_prod"
if [[ -f "$PROJECT_ROOT/.env.prod" ]]; then
    extracted_prod_db=$(extract_env "POSTGRES_DB" "$PROJECT_ROOT/.env.prod")
    if [[ -n "$extracted_prod_db" ]]; then
        PROD_DB_NAME="$extracted_prod_db"
    fi
fi

# Default target database is tutor_booking_restore_test
TARGET_DB="${TARGET_DB:-$DEFAULT_TARGET_DB}"

# Check for production restore attempt
is_prod_restore=false
if [[ "$TARGET_DB" == "$PROD_DB_NAME" || "$TARGET_DB" == "tutor_booking_prod" ]]; then
    is_prod_restore=true
fi
if [[ "$TARGET_ENV" == "prod" && "$TARGET_DB" == "$POSTGRES_DB" ]]; then
    is_prod_restore=true
fi

if [[ "$is_prod_restore" == "true" ]]; then
    if [[ "$DANGEROUSLY_RESTORE_TO_PROD" != "true" ]]; then
        echo "[ERROR] Refusing to restore to production database '${TARGET_DB}' without --dangerously-restore-to-production." >&2
        echo "        To restore into an isolated test database for verification, omit --target-db" >&2
        echo "        or specify '--target-db tutor_booking_restore_test'." >&2
        exit 1
    fi

    echo "================================================================================" >&2
    echo "WARNING: PRODUCTION RESTORE REQUESTED" >&2
    echo "You are about to completely DROP and RESTORE the production database:" >&2
    echo "  Target Database: ${TARGET_DB}" >&2
    echo "  Container:       ${CONTAINER_NAME}" >&2
    echo "================================================================================" >&2
    echo -n "Type '${TARGET_DB}' to confirm destruction and restoration: " >&2
    read -r confirm_input
    confirm_input="${confirm_input%$'\r'}"
    if [[ "$confirm_input" != "$TARGET_DB" ]]; then
        echo "[ERROR] Confirmation string did not match '${TARGET_DB}'. Restore aborted." >&2
        exit 1
    fi
fi

# Resolve companion or explicit media archive (BEFORE any destructive DB actions)
if [[ "$SKIP_MEDIA" == "true" ]]; then
    MEDIA_FILE=""
elif [[ -n "$MEDIA_FILE_EXPLICIT" ]]; then
    MEDIA_FILE="$(expand_tilde "$MEDIA_FILE_EXPLICIT")"
    if [[ ! -s "$MEDIA_FILE" ]]; then
        echo "[ERROR] Media backup file does not exist or is empty: ${MEDIA_FILE}" >&2
        exit 1
    fi
    echo "==> Verifying explicit media archive integrity with gzip -t..."
    if ! gzip -t "$MEDIA_FILE" 2>/dev/null; then
        echo "[ERROR] Media archive failed gzip integrity check: ${MEDIA_FILE}" >&2
        exit 1
    fi
    if ! tar -tzf "$MEDIA_FILE" &>/dev/null; then
        echo "[ERROR] Media archive failed tar content listing verification: ${MEDIA_FILE}" >&2
        exit 1
    fi
else
    # Auto-discover companion media archive if pattern matches backup_<TIMESTAMP>_db.sql.gz
    if [[ "$DB_FILE" == *"_db.sql.gz" ]]; then
        companion_candidate="${DB_FILE%_db.sql.gz}_media.tar.gz"
        if [[ -f "$companion_candidate" ]]; then
            MEDIA_FILE="$companion_candidate"
            echo "==> Discovered companion media archive: $(basename "$MEDIA_FILE")"
            if ! gzip -t "$MEDIA_FILE" 2>/dev/null; then
                echo "[ERROR] Companion media archive failed gzip integrity check: ${MEDIA_FILE}" >&2
                exit 1
            fi
            if ! tar -tzf "$MEDIA_FILE" &>/dev/null; then
                echo "[ERROR] Companion media archive failed tar listing: ${MEDIA_FILE}" >&2
                exit 1
            fi
        else
            echo "==> No companion media archive found alongside database snapshot."
            MEDIA_FILE=""
        fi
    else
        MEDIA_FILE=""
    fi
fi

# Resolve target media directory
if [[ -z "$TARGET_MEDIA_DIR" ]]; then
    TARGET_MEDIA_DIR="${MEDIA_DIR:-$PROJECT_ROOT/media}"
fi
TARGET_MEDIA_DIR="$(expand_tilde "$TARGET_MEDIA_DIR")"

# Pre-flight container and tools checks
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

# Avoid connecting to the target db while dropping it
if [[ "$TARGET_DB" == "$MAINTENANCE_DB" ]]; then
    MAINTENANCE_DB="template1"
fi

echo "=================================================="
echo "Starting Disaster Recovery Restore"
echo "  Environment:   ${TARGET_ENV} (${ENV_SOURCE})"
echo "  Container:     ${CONTAINER_NAME}"
echo "  Target DB:     ${TARGET_DB}"
echo "  Database User: ${POSTGRES_USER}"
echo "  Snapshot File: ${DB_FILE}"
if [[ -n "$MEDIA_FILE" ]]; then
    echo "  Media Archive: ${MEDIA_FILE}"
    echo "  Media Dest:    ${TARGET_MEDIA_DIR}"
elif [[ "$SKIP_MEDIA" == "true" ]]; then
    echo "  Media Archive: Skipped (--skip-media)"
else
    echo "  Media Archive: None (database only)"
fi
echo "=================================================="

# 1. Terminate existing connections to target database
echo "[1/3] Terminating active connections to '${TARGET_DB}'..."
"$DOCKER_BIN" exec "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$MAINTENANCE_DB" \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${TARGET_DB}' AND pid <> pg_backend_pid();" >/dev/null

# 2. Drop and recreate target database
echo "[2/3] Dropping and recreating target database '${TARGET_DB}'..."
"$DOCKER_BIN" exec "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$MAINTENANCE_DB" \
    -c "DROP DATABASE IF EXISTS \"${TARGET_DB}\";" >/dev/null

"$DOCKER_BIN" exec "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$MAINTENANCE_DB" \
    -c "CREATE DATABASE \"${TARGET_DB}\" OWNER \"${POSTGRES_USER}\";" >/dev/null

# 3. Stream decompressed SQL dump into target database
echo "[3/3] Restoring database snapshot into '${TARGET_DB}'..."
gzip -dc "$DB_FILE" | "$DOCKER_BIN" exec -i "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$TARGET_DB" >/dev/null
echo "      [OK] Database snapshot restored successfully into '${TARGET_DB}'."

# 4. Restore media assets if archive is available
if [[ -n "$MEDIA_FILE" && "$SKIP_MEDIA" != "true" ]]; then
    echo "==> Restoring media assets into '${TARGET_MEDIA_DIR}'..."
    mkdir -p "$TARGET_MEDIA_DIR"
    first_entry=$(tar -tzf "$MEDIA_FILE" 2>/dev/null | head -n 1 || true)
    if [[ "$first_entry" =~ ^(\./)?media(/|$) ]]; then
        tar --strip-components=1 -xzf "$MEDIA_FILE" -C "$TARGET_MEDIA_DIR"
    else
        tar -xzf "$MEDIA_FILE" -C "$TARGET_MEDIA_DIR"
    fi
    echo "      [OK] Media archive restored successfully into '${TARGET_MEDIA_DIR}'."
elif [[ "$SKIP_MEDIA" == "true" ]]; then
    echo "==> Skipping media restoration (--skip-media specified)."
fi

echo "=================================================="
echo "Restore Completed Successfully"
echo "  Target Database: ${TARGET_DB}"
echo "  Container:       ${CONTAINER_NAME}"
if [[ -n "$MEDIA_FILE" && "$SKIP_MEDIA" != "true" ]]; then
    echo "  Media Restored:  ${TARGET_MEDIA_DIR}"
fi
echo "=================================================="

exit 0
