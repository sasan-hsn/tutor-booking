# Feature Specification: Auto-Expire Pending Lesson Requests

## Problem Statement

When a student books a lesson, the booking is created in `pending` status, waiting for the teacher to either accept or decline the request. However, if a teacher does not take action before the scheduled start time arrives, the booking remains `pending` indefinitely.

From a teacher's perspective, this causes outdated requests to permanently clutter their "Pending Requests" badge counter and modal with buttons to accept or decline lessons that were supposed to happen in the past. Attempting to accept such requests would create invalid past-dated confirmed lessons.

From a student's perspective, an unreviewed request that passes its start time leaves the student in limbo. Crucially, if the request was for a trial lesson, the pending status permanently burns the student's one-time trial lesson eligibility with that teacher, even though the lesson never took place.

## Solution

1. Introduce an explicit `EXPIRED` status in the booking lifecycle (`Booking.Status.EXPIRED`).
2. Automatically transition any `pending` booking whose `start_at` timestamp is less than or equal to the current time (`start_at <= timezone.now()`) to `EXPIRED`.
3. Provide both an on-demand/lazy sweep upon dashboard/calendar/request access and a dedicated management command (`python manage.py expire_pending_bookings`) for automated batch processing.
4. Block teachers from accepting or declining expired requests at the API/view layer with a clear validation error.
5. Restore trial lesson eligibility for students whose trial lesson request expired without being accepted.
6. Display expired bookings on the calendar with a distinct terracotta visual indicator, hidden by default via an opt-in filter toggle to avoid cluttering active schedules.

## User Stories

1. As a teacher, I want pending lesson requests whose start time has passed to be marked as expired automatically, so that I am not asked to accept or decline lessons that can no longer happen.
2. As a teacher, I want the pending requests counter badge on my dashboard to exclude expired bookings, so that the badge accurately reflects only actionable requests.
3. As a teacher, I want the lesson requests modal to exclude expired bookings, so that I only see valid upcoming requests awaiting my decision.
4. As a teacher, I want to receive an informative error if I attempt to accept or decline a request that has already expired (e.g. from an outdated browser tab), so that I am not able to confirm lessons in the past.
5. As a student, I want my pending lesson request to be marked as expired once its scheduled start time passes without teacher confirmation, so that I have clear closure on the status of my booking.
6. As a student, I want my trial lesson eligibility to remain intact if a previous trial request expired without being accepted, so that I can re-book a trial lesson with the teacher.
7. As a student, I want to see the "Expired" status in my lesson details when viewing an expired booking, so that I know why the lesson did not take place.
8. As a teacher or student, I want expired bookings to be hidden by default on my calendar, so that my view remains uncluttered by non-events.
9. As a teacher or student, I want an "Expired" filter toggle in the calendar view, so that I can inspect past expired requests whenever I choose to.
10. As a teacher or student, I want expired bookings on the calendar to have a distinct visual style (terracotta accent), so that I can easily differentiate them from confirmed, completed, or cancelled bookings.
11. As a system operator, I want a management command to sweep and expire all past-due pending bookings in the database, so that data consistency can be maintained via scheduled tasks (e.g., cron or Celery beat) independent of user page views.

## Implementation Decisions

- **Status Enum Extension**:
  Extend `Booking.Status` with `EXPIRED = 'expired', 'Expired'` alongside existing statuses (`pending`, `confirmed`, `completed`, `cancelled`, `disputing`).

- **Database Migration**:
  Generate and apply a Django migration for `Booking.status` to include the new choice.

- **Central Expiration Service**:
  Add an `expire_stale_bookings(teacher=None, student=None)` utility in the booking services module.
  - Queries `Booking.objects.filter(status=Booking.Status.PENDING, start_at__lte=timezone.now())`.
  - Filters by `teacher` or `student` when provided in scoped contexts (such as loading a user dashboard).
  - Performs a bulk `.update(status=Booking.Status.EXPIRED)` to efficiently transition rows without unnecessary ORM overhead.
  - Returns the count of updated records.

- **Trial Eligibility Integration**:
  In `get_lesson_type_and_price()`, invoke `expire_stale_bookings(teacher=teacher, student=student)` prior to evaluating `has_previous_lesson`. Because `has_previous_lesson` checks `[PENDING, CONFIRMED, COMPLETED, DISPUTING]`, any expired trial request will no longer block trial pricing.

- **View-Level Guards & Sweeping**:
  - `teacher_dashboard` and `teacher_lesson_requests`: Call `expire_stale_bookings(teacher=teacher)` before computing `lesson_requests_count` or returning the request queryset.
  - `respond_to_booking`: Add a check verifying that `booking.status == Booking.Status.PENDING` and `booking.start_at > timezone.now()`. If `start_at <= timezone.now()`, transition the record to `EXPIRED` and respond with HTTP 400 (`{'error': 'This lesson request has expired.'}`).
  - `teacher_calendar`, `teacher_calendar_ajax`, `student_calendar`, `student_calendar_ajax`: Call `expire_stale_bookings()` before constructing the calendar grid.

- **Calendar Filtering & Styling**:
  - In `style.css`, style `[data-status="expired"]` using `--accent-terracotta` (`#C96442`).
  - In `teacher_calendar.html` and `student_calendar.html`, add an "Expired" checkbox under the status filter menu, unchecked by default.
  - In `main.js`, ensure the initial filter pass hides expired rows unless checked.

- **CLI Management Command**:
  Add `booking/management/commands/expire_pending_bookings.py` running `expire_stale_bookings()` across all teachers and students, outputting summary information.

## Testing Decisions

- **Definition of a Good Test**:
  Tests must assert user-visible and API-observable behavior without relying on internal implementation details. Tests should verify status codes, database state transitions, response payloads, and query counts/filters across realistic user workflows.

- **Modules and Flows to Test**:
  - `booking.models.Booking`: Ensure `Booking.Status.EXPIRED` is a valid choice and can be saved and queried.
  - `booking.services.expire_stale_bookings`:
    - Only expires `PENDING` bookings where `start_at <= now`.
    - Does not alter future `PENDING` bookings.
    - Does not alter past `CONFIRMED`, `COMPLETED`, or `CANCELLED` bookings.
    - Scoping by teacher/student correctly restricts updates.
  - `booking.views.respond_to_booking`:
    - Responding to an expired pending booking returns 400 and updates status to `EXPIRED`.
    - Responding to a valid future pending booking succeeds with 200.
  - `booking.views.teacher_dashboard` & `teacher_lesson_requests`:
    - Expired bookings do not appear in the count or list of pending requests.
  - `booking.services.get_lesson_type_and_price`:
    - Student with an expired trial request is still offered a trial lesson.
  - `booking.management.commands.expire_pending_bookings`:
    - Running `call_command('expire_pending_bookings')` transitions all eligible records and reports the correct count.

- **Prior Art**:
  Follows existing test patterns in:
  - `booking/tests/test_lesson_lifecycle.py` (`RoleTestCase` with `_make_booking` helpers)
  - `booking/tests/test_book_slot.py` (testing trial pricing and request constraints)
  - `booking/tests/test_services.py` (testing slot availability and pricing logic)

## Out of Scope

- Automated Celery beat scheduling (deferred until Celery & Redis are introduced in the architecture).
- Email or push notifications informing students/teachers when a booking expires (will be part of the future notifications/email milestones).
- Dynamic, teacher-configurable expiration deadlines (e.g. expiring 2 hours prior to start); expiration is strictly at `start_at`.

## Further Notes

- In accordance with project conventions, migrations will be generated with standard Django tools and committed.
- Git commits will adhere to Conventional Commits without any co-author or AI attribution trailers.
