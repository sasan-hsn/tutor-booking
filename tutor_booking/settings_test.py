"""
Dedicated test settings module enforcing real PostgreSQL execution.
"""

import os

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F401, F403

# Fast password hasher for tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# In-memory email backend for tests
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Celery tasks run synchronously during tests
CELERY_TASK_ALWAYS_EAGER = True

# Overrides for PostgreSQL test database
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
if not TEST_DATABASE_URL:
    raise ImproperlyConfigured(
        "TEST_DATABASE_URL or DATABASE_URL environment variable is required for tests. "
        "A real PostgreSQL database must be configured."
    )

DATABASES = {
    "default": dj_database_url.parse(
        TEST_DATABASE_URL,
        conn_max_age=0,
    )
}

if DATABASES["default"].get("ENGINE") != "django.db.backends.postgresql":
    raise ImproperlyConfigured(
        f"PostgreSQL database engine is required for tests (django.db.backends.postgresql), "
        f"but got {DATABASES['default'].get('ENGINE')}."
    )
