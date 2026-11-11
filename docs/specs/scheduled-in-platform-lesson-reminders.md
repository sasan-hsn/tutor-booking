# Feature Specification: Scheduled In-Platform Lesson Reminders via Celery Beat

## Problem Statement

When a student or teacher has an upcoming confirmed lesson, email reminders are sent at 24 hours and 1 hour before start time (ADR 0001). However, if either user is actively browsing or has their dashboard open, there are no real-time, in-platform reminders. Users must rely on checking external email inboxes or manually scanning their dashboard schedule to know when a lesson is imminent. Furthermore, clicking into the dashboard from a reminder provides no visual indication of which specific lesson card is starting soon.

## Solution

Extend the platform's real-time notification system (ADR 0007) and Celery Beat scheduler (ADR 0001) to automatically deliver persistent in-platform notifications alongside existing reminder emails:
1. Deliver a 24-hour advance lesson reminder in-platform to the student with standard informational level.
2. Deliver a 1-hour imminent lesson reminder in-platform to both the student and the teacher with elevated visual urgency (warning level).
3. Anchor notification links directly to the target lesson card on the student or teacher dashboard (`#booking-{booking.id}`), pairing this with a distinct CSS `:target` visual highlight pulse on the card.
4. Guarantee exact-once notification emission via defensive task-level idempotency checks while completely decoupling notification tasks from email delivery and retry lifecycles.
5. Invalidate and auto-mark pending reminder notifications as read if a lesson is cancelled.

## User Stories

1. As a student, I want to receive an in-platform notification 24 hours before my confirmed lesson, so that I am reminded of my upcoming class while browsing the site.
2. As a student, I want the 24-hour reminder to use clear, relative temporal phrasing ("scheduled for tomorrow"), so that I am not confused by absolute timezone differences.
3. As a student, I want to receive an urgent in-platform notification 1 hour before my confirmed lesson, so that I have immediate notice to prepare before the Lesson Join Window opens.
4. As a student, I want the 1-hour reminder notification to display an amber alert icon and warning styling, so that its imminent urgency stands out from routine updates.
5. As a teacher, I want to receive an urgent in-platform notification 1 hour before a lesson, so that I am alerted even if I haven't checked my daily schedule digest or calendar today.
6. As a teacher, I want 24-hour reminders suppressed for my account, so that my notification bell is not cluttered with advance notices for every scheduled lesson.
7. As a student or teacher, I want clicking a reminder notification in the dropdown or toast popup to open my dashboard and automatically scroll to the relevant lesson card.
8. As a student or teacher landing on the dashboard from a reminder notification, I want the targeted lesson card to pulse or highlight visually, so that I instantly locate it in my upcoming lessons carousel.
9. As a student or teacher, I want lesson reminder notifications to be delivered reliably without duplicates, even if background worker tasks are retried.
10. As a student who books a lesson on short notice (<24 hours before start), I want the 24-hour reminder notification to be automatically skipped, so that I do not receive an obsolete "scheduled for tomorrow" reminder.
11. As a student who books a lesson on same-day notice (<1 hour before start), I want to receive the 1-hour reminder on the next periodic sweep, so that I am reinforced that class is starting shortly.
12. As a student or teacher whose lesson has been cancelled, I want existing unread reminder notifications for that lesson to be marked as read automatically, so that my notification bell does not mislead me about a cancelled class.
13. As an offline student or teacher, I want unread reminder notifications to remain in my persistent notification dropdown when I log back in, so that I have an accurate audit trail of recent lesson reminders.

## Implementation Decisions

- **Notification Event Type Extension**:
  Extend `Notification.EventType` with `LESSON_REMINDER_24H = 'lesson_reminder_24h'` and `LESSON_REMINDER_1H = 'lesson_reminder_1h'`. A schema migration in the `notifications` app will register these choices.

- **System Actor Attribution**:
  All automated lesson reminder notifications will record `actor = None` to signify that they are platform-orchestrated background events rather than actions performed by the counterpart user.

- **Copy and Visual Levels**:
  - 24-hour student reminder: `level = Notification.Level.INFO`, title `"Upcoming Lesson Reminder"`, message `"Your {duration_minutes}-minute {lesson_type} with {teacher_display_name} is scheduled for tomorrow."`.
  - 1-hour student reminder: `level = Notification.Level.WARNING`, title `"Lesson Starting Soon"`, message `"Your {duration_minutes}-minute {lesson_type} with {teacher_display_name} starts in 1 hour."`.
  - 1-hour teacher reminder: `level = Notification.Level.WARNING`, title `"Lesson Starting Soon"`, message `"Your {duration_minutes}-minute {lesson_type} with {student_display_name} starts in 1 hour."`.

- **Decoupled Asynchronous Tasks**:
  Introduce three separate Celery tasks:
  - `send_booking_reminder_24h_student_notification_task(booking_id: int)`
  - `send_booking_reminder_1h_student_notification_task(booking_id: int)`
  - `send_booking_reminder_1h_teacher_notification_task(booking_id: int)`
  These tasks are enqueued alongside email tasks in the 5-minute `send_lesson_reminders` periodic sweep. Decoupling notification tasks from email tasks ensures that transient SMTP/API retries on email delivery never block or duplicate WebSocket alerts.

- **Task-Level Idempotency Protection**:
  Rather than adding new columns to the `Booking` model, each notification task will perform a defensive query before emission:
  `Notification.objects.filter(recipient=recipient, event_type=event_type, metadata__booking_id=booking.id).exists()`
  If a matching notification record exists, task execution exits cleanly without emitting duplicate WebSocket frames or records. Notification creation is wrapped in an atomic block.

- **Card Anchoring & CSS Target Pulse**:
  - Upcoming lesson card templates will attach `id="booking-{{ booking.id }}"` to the lesson card container element.
  - Notification `action_url` values will append `#booking-{booking.id}` to the dashboard reverse URL (`booking:student_dashboard` or `booking:teacher_dashboard`).
  - Add `.lesson-card:target` styling in the main stylesheet to smoothly highlight the border and box-shadow when the URL hash targets that card.

- **Cancellation Auto-Read Invalidation**:
  When a booking status transitions to `CANCELLED` during cancellation workflows, any existing unread `LESSON_REMINDER_24H` and `LESSON_REMINDER_1H` notifications for that booking will be marked as read (`is_read=True`) using the notification service helper, updating the recipient's unread counter in real time.

## Testing Decisions

- **Definition of a Good Test**:
  Tests must assert external observable behavior (database notification states, unread counts, WebSocket broadcasts, template rendered DOM IDs, and task idempotency) rather than private internal variables.
- **Modules to Test**:
  - Celery periodic task and async workers: Verify that `send_lesson_reminders` enqueues notification tasks for confirmed bookings in the 24h and 1h windows, excludes past or short-notice bookings, and remains completely idempotent across repeated runs.
  - Notification helper service: Verify that notifications are created with the correct recipient, level, title, relative message copy, metadata, and `#booking-{id}` action URL.
  - Cancellation lifecycle: Verify that cancelling a booking marks existing reminder notifications as read and updates badge counts.
  - Dashboard UI: Verify that upcoming lesson cards render with `id="booking-{booking.id}"`.
- **Prior Art**:
  `booking/tests/test_reminders.py`, `booking/tests/test_notifications.py`, and `notifications/tests/test_services.py`.

## Out of Scope

- Native browser push notifications (OS Service Worker API) or SMS alerts.
- Audio chimes or sound effects on incoming reminder toasts.
- In-platform reminders for unconfirmed pending bookings or expired requests.
- Customizable reminder intervals (e.g. user-configured 2-hour or 15-minute alerts).

## Further Notes

- Aligns directly with ADR 0001 (Celery & Redis), ADR 0006 (Lesson Join Window), and ADR 0007 (ASGI & Real-Time Notifications).
- Keeps `Booking` model lightweight without modifying the database table schema for reminder delivery tracking.
