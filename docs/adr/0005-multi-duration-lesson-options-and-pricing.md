# 5. Multi-Duration Lesson Options and Pricing Architecture

Date: 2026-10-03

## Status

Accepted

## Context

The booking flow previously assumed a single 50-minute regular lesson format alongside an optional 25-minute trial lesson. In real-world operation, students require the flexibility to book either a focused 25-minute lesson or a comprehensive 50-minute session at distinct rates established by the tutor.

Furthermore:
- First-time students must be able to choose between a Trial Lesson (25 min at trial price), a Regular Lesson (25 min at regular 25-min price), or a Regular Lesson (50 min at regular 50-min price), with the Trial Lesson pre-selected.
- Returning students (any student with a prior pending, confirmed, completed, or disputing booking) must not be offered the trial lesson; they must only see and choose between 25-minute and 50-minute regular lessons.
- The teacher's settings previously only stored `lesson_price` (50 min) and `trial_price` (25 min), with no mechanism to configure a 25-minute regular lesson rate.

## Decision

We will implement fixed multi-duration lesson options and explicit pricing fields across `TeacherProfile` and `Booking`:

1. **Explicit Pricing Fields on `TeacherProfile`**:
   - Retain `lesson_price` as the authoritative 50-minute regular rate.
   - Add `lesson_price_25` (`DecimalField`, nullable, min 0.00) for the 25-minute regular rate.
   - Fix lesson durations to platform standards: 25 minutes for trial and short regular lessons, and 50 minutes for full regular lessons. Remove arbitrary duration inputs from the teacher settings UI.
   - Enforce invariant validation in `TeacherBookingSettingsForm`: when pricing is active, `lesson_price_25` must be set and must not exceed `lesson_price` (`lesson_price_25 <= lesson_price`).
   - Update `TeacherProfile.booking_complete` to require both `lesson_price > 0` and `lesson_price_25 > 0`.

2. **Explicit `duration_minutes` Field on `Booking`**:
   - Add `duration_minutes` (`PositiveSmallIntegerField`) directly on `Booking` to store the lesson duration snapshot, populated at creation time.
   - Retain `lesson_type` as `'trial'` or `'regular'`, maintaining a clean separation between product category and temporal duration.
   - Accurately backfill existing database rows via data migration (`ROUND((end_at - start_at) in minutes)`).

3. **Trial Eligibility & Forfeiture**:
   - Trial eligibility strictly requires zero prior bookings in `PENDING`, `CONFIRMED`, `COMPLETED`, or `DISPUTING` statuses.
   - If an eligible first-time student explicitly selects and books a Regular Lesson (25m or 50m), their trial option is immediately forfeited for all future bookings.

4. **Authoritative Server-Side Validation for `book_slot`**:
   - The booking submission endpoint accepts `start_at` and a symbolic `lesson_option` key (`'trial'`, `'regular_25'`, or `'regular_50'`).
   - The server validates eligibility and dictates the price and duration from `TeacherProfile`. The client never submits prices or raw durations.
   - Availability checking verifies that candidate slots accommodate the requested duration (`duration_minutes`) without overlapping existing bookings or violating instant tutoring rules.

5. **Client Experience & Reactive Calendar Refresh**:
   - The student booking interface renders interactive selector cards at the top displaying title, duration, and price.
   - Pre-selects Trial Lesson for eligible students, and 50-minute Regular Lesson for returning students.
   - Selecting an option with a different duration triggers an AJAX reload of the day-picker grid to compute candidate start times matching that duration.
   - The confirmation modal renders a rich breakdown (avatar, name, lesson type, duration, price, and local date/time).
   - The public landing page displays a tiered pricing section detailing the 25-minute rate, 50-minute rate, and trial offering.

## Consequences

### Positive

- Gives students transparent choices between 25-minute and 50-minute sessions while maintaining full teacher autonomy over pricing.
- Prevents database churn and breaking changes by extending existing fields (`lesson_price_25`) rather than renaming existing columns.
- Prevents trial abuse with strict server-side eligibility checks and immediate forfeiture rules.
- Explicit `duration_minutes` simplifies reporting, notifications, and dashboard display without requiring runtime datetime arithmetic.

### Negative

- Switching durations on the booking page requires an AJAX round-trip to re-filter slots for the new duration (mitigated by fast, memoized server-side window computation).
