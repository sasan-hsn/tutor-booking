# 6. Lesson Join Window and Meeting Access Architecture

Date: 2026-10-05

## Status

Accepted

## Context

Confirmed bookings require a seamless mechanism for students and teachers to join their scheduled online lessons via the teacher's configured video conference URL (`meeting_link`). Previously, the meeting link was only distributed via email notifications (confirmation, 24h reminder, and 1h reminder) and was not directly accessible from the dashboard lesson cards or detail modals.

Key requirements and constraints:
- **Join Window Timing**: In 1-on-1 tutoring, students entering a meeting room too early (e.g. 15–20 minutes ahead) often find themselves alone in an empty call, creating anxiety that the teacher may have missed the lesson. Conversely, cutting access off strictly at `end_at` creates friction if a lesson runs slightly over time or if a user briefly disconnects during wrap-up. Furthermore, excessive pre-start buffers risk overlapping access windows for teachers with back-to-back lessons.
- **Surface Availability**: The dashboard carousel presents upcoming lesson cards (`300px` fixed width) where clicking any card opens the `lessonDetailModal`. When class is imminent, forcing a user through a multi-step modal interaction adds friction right when time is critical.
- **Client Dynamism**: Users frequently open their dashboard 15–20 minutes before a lesson and leave the browser tab open. Relying solely on server-rendered template evaluation would require users to manually refresh their page to see the join button activate.
- **Missing Infrastructure**: `TeacherProfile.meeting_link` is an optional field. A teacher could have confirmed lessons without having entered a meeting link in settings.

## Decision

We will implement the **Lesson Join Window** across the data model, templates, and client-side interactions:

1. **Explicit 10-Minute Buffer Bounds**:
   - The **Lesson Join Window** opens **10 minutes before `start_at`** (`start_at - 10 minutes`) and closes **10 minutes after `end_at`** (`end_at + 10 minutes`).
   - Defined via authoritative properties on the `Booking` model:
     - `JOIN_WINDOW_PRE_START_MINUTES = 10`
     - `JOIN_WINDOW_POST_END_MINUTES = 10`
     - `join_window_starts_at` (`DateTimeField` property: `start_at - 10m`)
     - `join_window_ends_at` (`DateTimeField` property: `end_at + 10m`)
     - `is_joinable` (`bool` property: `status == CONFIRMED` and `join_window_starts_at <= now <= join_window_ends_at`).
   - If the lesson is marked `COMPLETED` or `CANCELLED` during the post-end grace period, `is_joinable` immediately evaluates to `False`.
   - `Booking.is_live` remains strictly `status == CONFIRMED and start_at <= now < end_at`, preserving semantic accuracy for the red pulsing "Live Now" badge.

2. **Dual-Surface Placement (Card & Modal)**:
   - **Lesson Card**: When in the join window, a direct `"Join Lesson"` action button (`btn-accent btn-sm` with video icon) appears inside `.lesson-card-body`. Clicking the button opens the meeting link in a new tab (`target="_blank" rel="noopener noreferrer"`). Event handling uses `event.stopPropagation()` to prevent opening the `lessonDetailModal`.
   - **Detail Modal**: The `lessonDetailModal` (used by both Dashboard and Calendar views) prominently features a full-width `"Join Lesson"` primary button when joinable. Outside the join window, an informational row indicates `"Meeting link accessible 10 minutes before lesson"`.

3. **Handling Missing Meeting Links**:
   - **Teacher View**: If a lesson is joinable but the teacher has no `meeting_link` configured, the card and modal display a warning action: `"⚠️ Set Meeting Link"` pointing directly to `portfolio:teacher_settings_booking?next={dashboard_url}` so the teacher can configure their link and return immediately.
   - **Student View**: To avoid clutter and disabled buttons on compact cards, the button is omitted from the card. Inside the modal, a clear notice states: `"Teacher hasn't added a meeting link yet. It will appear here once configured."`

4. **Client-Side Real-Time Watcher**:
   - Upcoming lesson cards embed ISO 8601 timestamps and meeting metadata via dataset attributes (`data-start-at`, `data-end-at`, `data-meeting-link`, `data-status`, `data-is-teacher`).
   - A client-side watcher function (`initLessonJoinWindowWatcher()`) runs on page load and at 30-second intervals. It evaluates the local browser clock against the card timestamps, seamlessly revealing the `"Join Lesson"` button and updating the "Live Now" badge without requiring a manual page reload.

## Consequences

### Positive
- Zero-friction access: Students and teachers can jump into their lesson in a single click directly from the dashboard card or modal.
- Prevents premature waiting anxiety and back-to-back overlap by tightening the window to 10 minutes.
- Eliminates frantic manual page refreshing through lightweight 30-second DOM timer updates.
- Protects teachers against missing link oversights with an immediate actionable deep-link back to their settings.
- Reuses `lessonDetailModal` across both Dashboard and Calendar views, giving users access from both pages.

### Negative
- Client-side interval executes every 30 seconds (minimal CPU overhead on modern browsers for small lists of upcoming cards).
- If a client's system clock is significantly skewed, the DOM watcher relies on client time until the next page load.
