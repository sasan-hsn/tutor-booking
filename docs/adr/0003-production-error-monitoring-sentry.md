# 3. Production Error Monitoring and Observability with Sentry

Date: 2026-09-27

## Status

Accepted

## Context

As the platform transitions from an evaluated capstone project into an active commercial service deployed on a single VPS, operational observability is required to ensure system reliability.

Prior to this decision, diagnosing errors in production required SSH access to the VPS and manual inspection of Docker container logs (`docker compose logs`). Unhandled exceptions during HTTP requests resulted in generic 500 error pages without automated alerting. More critically, failures in asynchronous background operations—such as Celery tasks sending confirmation emails, expiring past-due bookings, or dispatching daily teacher digests—could fail silently without immediate developer notification.

While self-hosting an open-source error monitoring stack (e.g. Sentry on-premise or GlitchTip) was considered, running additional database and application containers would consume substantial CPU and RAM on the single-node VPS. Conversely, relying solely on Django's legacy `ADMINS` email handler lacks structured stack traces, user breadcrumbs, issue grouping, and release regression detection.

## Decision

We will integrate Sentry (`sentry-sdk`) using the Sentry Cloud Developer plan (free tier) across the backend services:

1. **Hosting & Provider**:
   - Use Sentry Cloud SaaS (`sentry.io`) on the free Developer tier. This eliminates operational maintenance, database backups, and memory consumption on the host VPS.
2. **Backend Scope & Boundaries**:
   - Instrument all backend execution contexts: Django WSGI (Gunicorn web processes), Celery Worker (asynchronous task execution), and Celery Beat (periodic scheduler).
   - Omit the client-side Sentry browser JavaScript SDK. For this server-rendered Django monolith, core business logic lives on the backend; omitting client-side tracking preserves free-tier quotas and prevents noise from third-party browser extensions or ad blockers.
3. **Quota Protection & APM Tracing**:
   - Set `traces_sample_rate = 0.0` (error monitoring only). Application Performance Monitoring (APM) transaction tracing is disabled to prevent quota exhaustion from Docker healthchecks (`/health/` every 60s) and frequent Celery beat intervals.
4. **Noise Immunity & Bot Defense**:
   - Explicitly ignore `django.core.exceptions.DisallowedHost` via `ignore_errors = [DisallowedHost]`. Automated web crawlers and malicious scanners hitting the VPS's raw IP address with unmapped `Host` headers will not consume Sentry event quotas or trigger false alerts.
5. **Data Privacy & PII Scrubbing**:
   - Enforce `send_default_pii = False` (the default). Raw student and teacher email addresses, passwords, and client IP addresses are masked and excluded from Sentry payloads.
   - When an authenticated user encounters an error, only their surrogate primary key (`user.pk`) is attached for diagnostic lookup in the internal database.
6. **Celery Task Resilience & Beat Monitoring**:
   - Celery's transient retry mechanism (`raise self.retry(exc=exc)`) is respected as normal fault-tolerant behavior; transient retries do not trigger Sentry error issues. Only unhandled exceptions or tasks that exhaust all retry attempts (`MaxRetriesExceededError`) generate error reports.
   - Celery Beat monitoring (`monitor_beat_tasks = True`) is enabled to detect if the periodic scheduler container hangs or stops ticking.
7. **Environment-Driven Initialization & Test Suite Isolation**:
   - Initialize Sentry in `tutor_booking/settings.py` conditioned on the presence of `SENTRY_DSN`. When `SENTRY_DSN` is empty (default in local development and CI), the SDK remains completely inert.
   - Explicitly bypass initialization during automated test execution (`'test' in sys.argv`), ensuring tests run fast, offline, and without external network side effects.
   - Set `environment = os.getenv('SENTRY_ENVIRONMENT', 'production' if not DEBUG else 'development')`, with optional `SENTRY_RELEASE` support.

## Consequences

### Positive

- Instant email alerts on any unhandled production exception across both web requests and background Celery tasks.
- Zero CPU, memory, or disk storage overhead on the production VPS.
- Strict quota preservation ensuring the free tier comfortably covers the single-teacher platform.
- Full privacy compliance with user PII (emails, passwords, IPs) stripped from third-party logs.
- Test suite and local development remain completely offline and self-contained when `SENTRY_DSN` is absent.

### Negative

- Introduces a runtime third-party dependency (`sentry-sdk`) in `requirements.txt`.
- Sentry Cloud free tier imposes monthly event quotas (e.g. 5,000–10,000 error events), requiring proactive noise filtering (such as ignoring `DisallowedHost`).
