# English with Marry — Tutor Booking & Portfolio Platform

A Django + JavaScript web application that lets a private tutor run a public
portfolio landing page and a full lesson-booking system, with separate
dashboards for the teacher and each student. Built as the CS50W final
project (Capstone).

## Distinctiveness and Complexity

This project is a scheduling and CRM-style platform for a private tutor, not
a social network and not an e-commerce store, and it is meaningfully more
complex than Projects 1 through 4 in this course.

**Why it isn't an e-commerce site.** At a glance, a page that lists a
lesson price and a "book" button can look like a shopping cart, so it's
worth being explicit about why it isn't one. An e-commerce site is built
around a *catalog* of interchangeable items and a *cart* checkout flow: add
item, adjust quantity, pay, done. This project has no catalog and no cart.
What it actually models is a **calendar of finite, mutually exclusive time
slots** shared between two parties, each of whom has a different timezone,
a different view of "now," and different permissions over the same
underlying record. A booking isn't a line item — it's a state machine
(`pending → confirmed → completed/cancelled/disputing`) that both the
teacher and the student can transition in different ways depending on
timing and role. There is no inventory to decrement; there is a continuous
interval of time that has to be checked for overlap against every other
booking, in real time, under concurrent access. That is a scheduling
problem, not a shopping problem, and the two are solved with entirely
different data models and logic.

**Interval-based availability, not pre-generated slots.** Many booking
tutorials pre-generate fixed appointment slots in the database. This
project does the opposite: a teacher's `RegularAvailability` and
`WeeklyOverride` records store open time *ranges*, and `booking/services.py`
computes bookable start times on the fly, at a configurable lesson
duration, by walking those ranges in fixed steps and excluding anything
that overlaps an existing active booking or falls in the past. This means
availability, lesson duration, and existing bookings are always the single
source of truth — there's no separate slot table that can drift out of
sync with reality.

**Timezone correctness from the viewer's perspective.** Every booking is
stored in UTC, but the teacher and each student can be in different
timezones, and the site must show each of them the *correct local time*
for the same booking — not the timezone of whoever created the record.
`Booking` exposes both `teacher_local_start`/`student_local_start` and the
equivalent `_end` properties, and every availability calculation is
anchored to the teacher's own calendar day, which matters when a teacher's
"today" and a student's "today" briefly disagree across a date boundary.
An "Instant Tutoring" feature layered on top of this lets a teacher
optionally allow same-day bookings with a one-hour buffer from the current
moment — which only works correctly because the underlying time math is
already timezone-aware.

**Concurrency safety.** Two students can attempt to book the same slot at
the same moment. The `book_slot` view re-derives availability inside a
database transaction with `select_for_update()`, backed by a database-level
`UniqueConstraint` on `(teacher, start_at)` for active bookings, so a race
condition results in one booking succeeding and the other cleanly rejected
— never a double-booked slot. This is verified by an actual concurrent HTTP
request test, not just a unit test of the model in isolation.

**Three independent Django apps with real separation of concerns.**
`accounts` owns authentication, roles, and cross-cutting signals;
`booking` owns the scheduling engine, lesson lifecycle, and reviews;
`portfolio` owns the public-facing landing page and the teacher's
self-service settings (account, portfolio content, and pricing, each its
own page, each saving independently). A `post_save` signal centrally
guarantees that every user of a given role always has the matching profile
record, regardless of which code path created them — signup form, Django
admin, or a management command.

**A real teacher onboarding flow.** Beyond CRUD, the project implements a
non-mandatory, checklist-driven onboarding: a teacher can sign up, land in
an empty dashboard immediately (no forced wizard), and see a dismissible
completion banner pointing at whichever of Account/Portfolio/Booking
settings still need attention — computed from live model properties, not a
stored flag that can go stale.

**JavaScript-driven front end, no framework.** All interactivity —
AJAX-loaded modals, the availability day-picker, calendar filtering,
schedule editors, live review submission, and horizontally-scrolling card
carousels — is hand-written vanilla JavaScript using `fetch()` against
Django views, with no React/Vue/build step, matching the vanilla JS
constraint used throughout this course while still exercising a
substantial amount of dynamic, asynchronous client-server interaction.

Taken together — a real interval-based scheduling engine, dual-timezone
correctness, transactional concurrency control, a multi-app architecture
with a centralized identity-consistency guarantee, and a from-scratch
onboarding flow — this project goes well beyond CRUD-with-a-price-tag and
is not fairly described as either a social network or a storefront.

## What's contained in each file

- **`accounts/`** — Custom `User` model with a `role` field (student/
  teacher) and a `timezone` field. `signals.py` auto-creates the matching
  `StudentProfile`/`TeacherProfile` on user creation. `decorators.py`
  provides `student_required`/`teacher_required`, enforced with a 403 on
  the wrong role. `forms.py` and `views.py` handle student signup, teacher
  signup, login/logout, and the student's own profile settings.
  `management/commands/create_teacher.py` is an alternate, script-based
  way to seed the platform's teacher account.

- **`booking/`** — The scheduling engine. `models.py` defines `Booking`,
  `RegularAvailability`, `WeeklyOverride`, and `Review`, each with
  `clean()`-level business-rule validation (no overlapping active
  bookings, no self-booking, valid time ranges, etc.). `services.py`
  computes availability windows and bookable start times, including
  instant-tutoring and past-time exclusion logic. `views.py` covers both
  dashboards (teacher and student), the booking flow itself, the
  day-picker AJAX endpoints, the monthly calendar with filtering, lesson
  lifecycle transitions (cancel/complete/mark-not-held), lesson request
  accept/decline, and the review submission/approval flow. `tests/`
  contains close to 100 tests spanning model validation, service logic,
  view-level behavior, access control, and a real concurrency test.

- **`portfolio/`** — The public side of the site. `models.py` defines
  `TeacherProfile` (bio, pricing, contact info, Instant Tutoring toggle,
  onboarding-completion properties) and `Certificate`. `views.py` renders
  the dynamic landing page and the teacher's three independent settings
  pages (Account, Portfolio, Booking). `context_processors.py` exposes the
  primary teacher to every template site-wide, for the navbar and footer.

- **`templates/`** — Server-rendered Django templates, split into
  dashboard, landing, and account templates, with small `partials/`
  fragments returned directly by AJAX endpoints for modal content
  (lesson detail, pending reviews, schedule editors).

- **`static/js/main.js`** — All client-side behavior: modal loading,
  horizontal card-carousel scrolling, the availability day-picker, week
  navigation, the schedule editor grids, calendar filtering, and
  AJAX form submissions with CSRF handling, all as vanilla JS event
  delegation against the server-rendered DOM.

- **`static/css/style.css`** — All styling, including a small design-token
  system (CSS custom properties for color and spacing) and a fully
  responsive layout down to mobile widths.

## How to run the application

1. Clone the repository and create a `.env` file in the project root:

   ```
   SECRET_KEY=your-secret-key
   DEBUG=True
   ALLOWED_HOSTS=127.0.0.1,localhost
   DATABASE_URL=postgresql://postgres:postgrespassword@127.0.0.1:6543/tutor_booking_db
   ```

   The `DATABASE_URL` line is optional. If it's omitted, the app falls
   back automatically to a local `db.sqlite3` file — no code changes
   required, which is the easiest path for grading.

2. If using Postgres, start the database with Docker:

   ```
   docker-compose up -d
   ```

3. Install dependencies and set up the database:

   ```
   pip install -r requirements.txt
   python manage.py migrate
   ```

4. Create the platform's teacher account (or use the in-app "Sign up as a
   teacher" flow from the homepage instead):

   ```
   python manage.py create_teacher --username teacher --email teacher@example.com --password yourpassword
   ```

5. Run the server:

   ```
   python manage.py runserver
   ```

   Visit `127.0.0.1:8000` for the public landing page, or sign up as a
   student to try the booking flow end to end.

## Additional information

The test suite (`python manage.py test`) covers 94 tests across
all three apps, including model validation, availability/timezone
calculations, view-level behavior, role-based access control on every
protected URL, and a genuine concurrency test against `book_slot`. Payment
processing and video-call integration are intentionally out of scope for
this submission; the booking system tracks price and lesson type but does
not move real money, which keeps the project focused on the scheduling
logic itself.
