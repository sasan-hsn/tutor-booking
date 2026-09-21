# Feature Specification: Celery + Redis Background Task Infrastructure & Automated Booking Email Notifications

## Problem Statement

Mary has reached the operational limits of Calendly and needs this custom platform to serve as her primary, self-hosted scheduling system. However, the application currently lacks automated email communication for the booking lifecycle. 

When a student submits a lesson request, confirms a lesson, or cancels, neither party receives an email confirmation, meeting details, or schedule reminders. This forces Mary and her students to manually coordinate lesson times and video links across external messaging apps, leading to administrative overhead, missed appointments, and poor user experience. 

Furthermore, attempting to send emails synchronously within the web request cycle adds user-facing latency and introduces critical failure risks if the remote email server encounters transient delays.

## Solution

1. Introduce an asynchronous task queue using Celery and Redis to handle email dispatches reliably in the background with automatic retries and exponential backoff.
2. Deploy Celery Beat to execute periodic background sweeps for automated lesson reminders (24 hours and 1 hour before scheduled start time) and automated pending booking expiration.
3. Add a dedicated video meeting link to the teacher's profile settings with graceful fallback messaging in email templates.
4. Deliver automated, responsive HTML and plain-text emails for all core booking lifecycle events (lesson requested, booking confirmed, booking cancelled/declined, and lesson reminders), formatted with accurate localized dates and times for each recipient.
5. Attach a standard `.ics` iCalendar invite to lesson confirmation emails so students can add scheduled lessons to Google Calendar, Apple Calendar, or Outlook with a single click.
6. Provide idempotent background sweeps using database tracking flags on bookings so that reminders are never duplicated and cancelled lessons never send phantom reminders.

## User Stories

1. As a student, I want to receive an email immediately after submitting a lesson request, so that I have written confirmation that my request was submitted and is pending teacher review.
2. As a teacher, I want to receive an email notification when a student submits a new lesson request, so that I know to review and accept or decline it promptly.
3. As a teacher, I want the new lesson request email to display the lesson time in my local timezone and provide a direct link to my dashboard, so that I can take action with minimal friction.
4. As a student, I want to receive a confirmation email when the teacher accepts my lesson request, so that I know my lesson is officially scheduled.
5. As a student, I want my lesson confirmation email to show the lesson date and time in my local timezone with the timezone name clearly indicated, so that there is no confusion about when to attend.
6. As a student, I want my lesson confirmation email to contain a direct "Join Lesson" link to the teacher's video classroom, so that I can easily enter the lesson at the scheduled time.
7. As a student, I want my lesson confirmation email to include a standard calendar attachment (.ics), so that I can add the lesson directly to my phone or computer calendar with one tap.
8. As a student, I want the calendar event imported from the email to automatically match my device's timezone and include the video meeting URL in the location field, so that my calendar notifications take me directly to the lesson.
9. As a student, I want to receive a polite email notification if the teacher declines my lesson request, so that I know the slot was unavailable and can select an alternative time.
10. As a student, I want to receive an email notification if the teacher cancels a confirmed lesson, so that I am immediately aware of the cancellation and do not wait in an empty classroom.
11. As a teacher, I want to receive an email notification if a student submits a cancellation request for a lesson, so that I can review and confirm the cancellation in my dashboard.
12. As a student, I want to receive an email reminder 24 hours before my confirmed lesson begins, so that I have advance notice to prepare for our session.
13. As a student, I want to receive an email reminder 1 hour before my confirmed lesson begins, so that I have an immediate prompt and meeting link ready when lesson time arrives.
14. As a teacher, I want to receive an automated email reminder 1 hour before scheduled lessons with the classroom meeting link, so that I have an operational prompt right before class, while avoiding alert fatigue from individual 24-hour reminders across multiple daily lessons (with tomorrow's schedule summarized via a consolidated Daily Digest instead).
15. As a student or teacher, I want to never receive duplicate reminder emails for the same lesson, so that my inbox is not spammed.
16. As a student or teacher, I want to never receive reminder emails for a lesson that has been cancelled or expired, so that I am not misled by outdated notifications.
17. As a teacher, I want to be able to save my video meeting link (e.g. Google Meet or Zoom URL) in my profile settings, so that all future booking emails automatically include my classroom link.
18. As a student, I want the confirmation email to provide helpful fallback guidance if the teacher has not yet configured a permanent meeting link, so that I understand how the teacher will connect with me.
19. As a website visitor or user booking a lesson, I want the web page to respond immediately without hanging while an email is being sent, so that my booking experience feels fast and reliable.
20. As a system administrator, I want transient email delivery failures to be automatically retried in the background with exponential backoff, so that temporary SMTP issues do not cause permanent loss of notifications.
21. As a system administrator, I want past-due pending lesson requests to be automatically swept and expired periodically, so that the database remains consistent without relying solely on user page requests.

## Implementation Decisions

- **Asynchronous Task Architecture**:
  - Introduce Celery configured to use Redis as its message broker and result backend.
  - In production deployment, run Redis, a Celery worker, and a Celery Beat scheduler as managed services alongside Postgres and Gunicorn.
  - Restrict Redis memory usage and eviction policy to protect VPS host resources.
  - Constrain Celery worker concurrency to avoid excessive RAM consumption on the server.
  - In automated test suites and local development fallback, configure Celery in eager execution mode so tasks execute synchronously in-memory without requiring a running Redis daemon.

- **Transactional Task Dispatch**:
  - Dispatches to background tasks from HTTP views must occur inside database transaction commit hooks (`transaction.on_commit`), ensuring that messages are only enqueued once the triggering database row changes are committed.
  - Celery task arguments must strictly use primitive types (e.g. database primary keys like `booking_id`) rather than serialized model instances, re-querying the database within the task to avoid stale data.

- **Fault Tolerance and Retries**:
  - Email sending tasks must define automatic retries with exponential backoff for transient SMTP or network exceptions, capped at a defined maximum attempt count.

- **Teacher Profile Meeting Link**:
  - Extend the teacher profile schema with an optional URL field for the video meeting room.
  - Expose this field in the teacher's profile settings form.
  - If populated, confirmation and reminder emails render a primary call-to-action button linking directly to the video classroom.
  - If unpopulated, email templates render a clean fallback message indicating that the teacher will provide meeting instructions prior to lesson start.

- **Idempotent Reminder Sweeps**:
  - Extend the booking schema with two boolean tracking flags: `reminder_24h_sent` and `reminder_1h_sent` (defaulting to `False`).
  - Index booking start time and status to ensure high-performance periodic queries.
  - Celery Beat executes a periodic reminder task on a 5-minute interval:
    - **24h Window**: Queries confirmed bookings in the 24h window (bounded so bookings confirmed <24h away do not fire a 24h reminder) where `reminder_24h_sent` is `False`. Sends 24h reminder to **student only**.
    - **1h Window**: Queries confirmed bookings in the 1h window (`now < start_at <= now + 1h`) where `reminder_1h_sent` is `False`. Sends 1h reminder to **both student and teacher** with the direct classroom meeting link.
    - Atomically updates tracking flags to prevent duplicate dispatch across consecutive ticks.
  - Bookings that are cancelled, completed, or expired are naturally excluded from the query, preventing phantom reminders.

- **Automated Expiration Sweep**:
  - Celery Beat executes a periodic task on a 15-minute interval that runs the central stale booking expiration routine, automatically transitioning past-due pending requests to expired status.

- **Teacher Daily Schedule Digest Sweep**:
  - Celery Beat executes an hourly periodic sweep (`send_daily_schedule_digests`) evaluating whether a teacher's local time matches the evening briefing window (20:00 local time).
  - For matching teachers, aggregates all confirmed lessons scheduled for tomorrow in the teacher's local timezone.
  - Sends a consolidated responsive HTML and plain-text briefing (`teacher_daily_digest.html` / `.txt`) with meeting links, student names, and chronological schedules.
  - Persists idempotent dispatch records (`TeacherDailyDigestRecord`) with database-level uniqueness constraints on `(teacher, target_date)` to prevent duplicate deliveries across retries or repeated periodic ticks.

- **Email Templating and Formatting**:
  - Standardize all booking emails on multipart format (HTML and plain text).
  - Match typography and color tokens with the site design system (Inter, Outfit, primary blue, and accent amber).
  - Explicitly format dates and times using the recipient's personal local timezone, displaying both the localized time and the timezone abbreviation or identifier.

- **iCalendar (.ics) Generation**:
  - For lesson confirmation emails, dynamically generate an RFC 5545 compliant `.ics` calendar invite.
  - All timestamps in the calendar invite (`DTSTART`, `DTEND`, `DTSTAMP`) must be represented in UTC with the trailing `Z` designator, ensuring calendar applications across all platforms correctly translate the event to the user's device timezone.
  - Populate event summary, detailed description with lesson type and student name, and the event location with the teacher's meeting link.

## Testing Decisions

- **Testing Philosophy**:
  - Tests must assert observable external behavior: HTTP status codes, database state transitions, and messages captured in the Django email test outbox.
  - Tests must never mock internal private functions or assert private Celery internals.

- **Test Seam 1: HTTP View Layer**:
  - Exercise the booking submission, teacher response, cancellation, and student cancellation request endpoints using the Django test client.
  - Assert that appropriate HTML and plain text emails appear in `mail.outbox`.
  - Validate recipient addresses, subject lines, localized timestamps, presence of meeting links, and the valid structure and content of `.ics` attachments on confirmation emails.

- **Test Seam 2: Periodic Scheduled Tasks**:
  - Directly invoke the periodic Celery task functions with seeded bookings positioned across different time horizons.
  - Assert that 24-hour and 1-hour reminder emails are delivered to both student and teacher when eligible.
  - Assert idempotency: executing the sweep task consecutively must result in zero additional emails sent and unchanged flags.
  - Assert that non-confirmed bookings (cancelled, expired, pending) do not receive reminders.
  - Assert that the periodic expiration task transitions stale pending requests to expired status.

- **Prior Art**:
  - Follows existing test conventions in `accounts/tests/test_views.py` (which tests email outbox delivery for password resets) and `booking/tests/test_lesson_lifecycle.py` (which uses `RoleTestCase` to test role-based booking operations).

## Out of Scope

- Direct Google Meet or Zoom OAuth API integrations that auto-generate unique video rooms per booking (recorded in project backlog for future evaluation).
- In-app push notifications or browser notifications.
- SMS or WhatsApp notification channels.
- Multi-teacher notification routing (the application remains single-tenant for Mary).

## Further Notes

- Architectural decisions, rationale, and consequences are formally documented in ADR 0001 (`docs/adr/0001-celery-redis-email-notifications.md`).
- Domain terms (Lesson Reminder, Meeting Link) are cataloged in `CONTEXT.md`.
