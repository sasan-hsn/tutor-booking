# 1. Use Celery and Redis for Email Notifications and Scheduled Tasks

Date: 2026-09-20

## Status

Accepted

## Context

The platform is transitioning into the primary scheduling system for Mary's tutoring business, replacing Calendly. Reliable automated communication (booking notifications, cancellation alerts, and 24h/1h lesson reminders) is essential to prevent missed lessons and reduce manual administrative overhead.

Sending emails synchronously within the Django request-response cycle introduces user-facing latency (~0.5–1.5s per email) and risks timing out or failing user transactions if the remote SMTP server experiences transient hiccups. Furthermore, time-based reminders (24 hours and 1 hour before a lesson) and scheduled sweeps (expiring past-due pending requests) require a reliable background scheduler.

## Decision

We will introduce Celery with Redis as the message broker and Celery Beat as the periodic scheduler:

1. **Redis Broker**: Deploy Redis in Docker Compose with memory constraints (`maxmemory 128mb`, `noeviction`) to preserve VPS resources.
2. **Celery Worker**: Handle asynchronous email dispatch with automated retries and exponential backoff (`max_retries=3`, `countdown=60`) on SMTP failures.
3. **Celery Beat**: Run periodic sweeps:
   - Every 5 minutes for upcoming lesson reminders (24-hour and 1-hour windows).
   - Every 15 minutes for expiring pending lesson requests whose start time has passed.
4. **Idempotent Reminders via Database Flags**: Instead of Celery ETA tasks (which live in volatile broker memory and are difficult to revoke upon cancellation), use `reminder_24h_sent` and `reminder_1h_sent` boolean flags on the `Booking` model. The periodic sweep queries for unnotified confirmed bookings and updates flags atomically.
5. **Transactional Safety**: All asynchronous task dispatches in views are wrapped in `transaction.on_commit()` and pass primitive identifiers (`booking_id`) rather than serialized model instances.
6. **Local Development and Testing**: `CELERY_TASK_ALWAYS_EAGER = True` is used in automated test runs to execute tasks synchronously in-memory, avoiding an external Redis requirement for tests.

## Consequences

### Positive

- Zero user-facing latency during booking, response, or cancellation actions.
- Automatic retries handle transient SMTP errors without user interruption.
- Dedicated periodic scheduler automates both lesson reminders and pending booking expiration.
- Database-flag approach guarantees that cancelled or rescheduled lessons never send stale reminders, even across broker/worker restarts.
- Test suite remains fast and self-contained with eager task execution.

### Negative

- Increases Docker infrastructure complexity on the VPS by introducing three new containers (`redis`, `celery_worker`, `celery_beat`).
- Increases server memory footprint (~100–150MB across Redis and Celery processes), mitigated by bounded Redis memory and constrained worker concurrency.
