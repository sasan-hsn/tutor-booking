# Feature Specification: Production Error Monitoring (Sentry)

## Problem Statement

When the platform runs on the production VPS, unexpected errors or exceptions can happen in three distinct runtime environments:
1. **Web Requests (Gunicorn/WSGI)**: An unhandled exception during a view render or API call results in a standard 500 server error response to the user, but without active notification to the developer.
2. **Asynchronous Tasks (Celery Worker)**: Failures in background tasks (e.g. failing to send a lesson confirmation or reminder email, transactional API failures) execute silently inside worker processes.
3. **Periodic Scheduling (Celery Beat)**: If the scheduler container dies or crashes, periodic jobs (lesson reminders, pending booking auto-expiration, teacher daily digests) cease executing without any user or developer visibility.

Manual inspection of container logs via SSH is reactive and insufficient for production reliability. An automated error tracking system is required to capture unhandled exceptions with full stack traces, breadcrumbs, and instant alerts.

## Solution

Integrate `sentry-sdk` into Django and Celery:
1. Add `sentry-sdk` to `requirements.txt`.
2. Configure Sentry initialization in `tutor_booking/settings.py` when `SENTRY_DSN` is set and not running in the automated test runner.
3. Set `traces_sample_rate = 0.0` (error monitoring only; no APM performance transaction overhead).
4. Ignore `django.core.exceptions.DisallowedHost` to eliminate bot scan noise.
5. Enforce privacy with `send_default_pii = False` and surrogate primary key user context.
6. Enable Celery integration with `monitor_beat_tasks = True`.
7. Add automated tests verifying initialization invariants and configuration parameters.

## User Stories

1. As a developer, I want to receive an email notification when an unhandled 500 error occurs on the live site, so that I can diagnose and patch production bugs immediately.
2. As a developer, I want unhandled background exceptions in Celery tasks to be captured in Sentry with their task arguments and stack traces, so that background email or scheduling failures do not go unnoticed.
3. As a developer, I want normal Celery task retries to be treated as expected resilience rather than error alerts, so that temporary third-party API glitches do not generate false alarms.
4. As a developer, I want Sentry to alert me if the Celery Beat scheduler stops running, so that missed reminders or auto-expiration sweeps can be promptly addressed.
5. As a developer, I want port/IP scanner noise (`DisallowedHost`) to be ignored, so that bot activity does not consume Sentry free tier quotas or trigger unnecessary alerts.
6. As a student or teacher, I want my personal data (email, password, IP) to remain private and never be forwarded to third-party monitoring platforms.
7. As a developer running tests locally or in CI, I want the test suite to execute fast and offline without attempting to connect to Sentry.

## Implementation Details

- **Dependency**:
  Add `sentry-sdk>=2.0.0,<3.0.0` (or pinned version) to `requirements.txt`.

- **Settings (`tutor_booking/settings.py`)**:
  ```python
  import sentry_sdk
  from sentry_sdk.integrations.django import DjangoIntegration
  from sentry_sdk.integrations.celery import CeleryIntegration
  from sentry_sdk.integrations.logging import LoggingIntegration
  import logging
  from django.core.exceptions import DisallowedHost

  SENTRY_DSN = os.getenv('SENTRY_DSN', '')

  if SENTRY_DSN and 'test' not in sys.argv:
      sentry_sdk.init(
          dsn=SENTRY_DSN,
          integrations=[
              DjangoIntegration(),
              CeleryIntegration(monitor_beat_tasks=True),
              LoggingIntegration(
                  level=logging.INFO,        # Capture info and above as breadcrumbs
                  event_level=logging.ERROR, # Send errors as events
              ),
          ],
          environment=os.getenv('SENTRY_ENVIRONMENT', 'production' if not DEBUG else 'development'),
          release=os.getenv('SENTRY_RELEASE', None),
          traces_sample_rate=float(os.getenv('SENTRY_TRACES_SAMPLE_RATE', '0.0')),
          send_default_pii=False,
          ignore_errors=[DisallowedHost],
      )
  ```

- **Environment Configuration**:
  Document `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, and `SENTRY_RELEASE` in `.env.example` or documentation.
