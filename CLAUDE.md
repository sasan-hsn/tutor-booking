# Project Context: tutor_booking (englishwithmary.ir)

This document is a full history/context handoff for an AI coding assistant picking up this project. Read this before making any changes — it explains *why* things are built the way they are, not just what exists.

## About the developer (context for how to communicate)
- Career-transitioner into software development, self-taught, learning Django/Python over the past ~1-2 years (CS50 Python, CS50 Web completed).
- Prefers being taught, not just handed code: explain the "why" before the "how" for any non-trivial change.
- Comfortable with Git branching/PR workflow (see Git Workflow below) — already has good habits here, keep using them.
- Currently at beginner-to-intermediate Linux/DevOps skill level, learned Docker/VPS deployment hands-on during this project (with heavy step-by-step guidance).

## Project overview
- **What it is:** A personal branding + online teaching management platform for a single English teacher (the developer's wife). NOT a multi-teacher marketplace (not Preply/AmazingTalker-style) — single-tenant by design for now.
- **Core features:** Public landing/portfolio page (bio, testimonials, pricing), student booking flow (view availability, book lessons, manage appointments), teacher dashboard (availability management, bookings, calendar, student roster).
- **Origin:** Built as the final Capstone project for Harvard's CS50W (Web Programming with Python and JavaScript). Submission is now complete. The project has since moved into an "industrial phase" — adding features beyond CS50 scope for real-world production use.
- **Repo:** `sasan-hsn/tutor-booking`
- **Live site:** https://englishwithmary.ir

## Tech stack
- **Backend:** Django (monolith, server-rendered templates — no separate frontend framework)
- **Frontend:** Vanilla JS (Fetch API/DOM), Bootstrap-based styling, custom CSS design tokens (Inter + Outfit fonts, `--primary-blue`/`--accent-amber` CSS variables)
- **Database:** PostgreSQL (via Docker; SQLite fallback via `dj_database_url` for portability/CS50 grading)
- **Auth:** Django's built-in session-based auth (deliberately NOT JWT — see Architecture Decisions below)
- **Deployment:** Docker Compose on a VPS, Nginx (host-level, not containerized) as reverse proxy, Gunicorn as WSGI server, Let's Encrypt/Certbot for HTTPS

## Data model summary
- Custom `User` model (`accounts.User`) with a `role` field (student/teacher) and a `timezone` CharField (IANA timezone string, validated via `zoneinfo.available_timezones()`)
- `TeacherProfile` / `StudentProfile` — one-to-one with `User`
- `RegularAvailability` / `WeeklyOverride` — recurring/one-off availability rules for teachers. **Intentionally stored as naive times** (they're recurring rules, not fixed instants — DST-safe by design). Every consumer must explicitly convert using the teacher's timezone.
- `Booking` — the core scheduling entity. Uses **interval-based booking** (Calendly/Cal.com-style): there is no `AvailabilitySlot` model; bookable times are computed on-demand via `booking/services.py`.
- `Review` — one-to-one with `Booking`, requires teacher approval (`is_approved`) before showing publicly.

## Key architectural decisions (and why)

### 1. Interval-based booking, not pre-generated slots
Early design used an `AvailabilitySlot` model; this was deleted and replaced with on-demand computation (`booking/services.py`):
- `get_availability_windows()` — applies the "Replacing" WeeklyOverride rule on top of RegularAvailability
- `get_available_start_times()` — checks 30-min candidates against windows minus active bookings; also filters out past times (`current_dt > now`) and, for the teacher's current calendar day, applies the Instant Tutoring rule (see below)
- `get_lesson_type_and_price()` — trial vs regular pricing
- `get_week_data()` — shared by the day-picker view and its AJAX week-nav endpoint; handles timezone spillover (a slot in the teacher's timezone can land on a different calendar day in the student's timezone)

### 2. Concurrency safety for bookings
Uses `select_for_update()` on the **TeacherProfile row** (not the Booking row — a new booking can't lock a row that doesn't exist yet) inside an atomic transaction, backstopped by a conditional `UniqueConstraint` on `Booking`. Known limitation (documented in README): the constraint only guarantees no duplicate *exact* start_time; full overlap protection is Postgres-only.

### 3. Timezone architecture — full UTC-storage overhaul
Originally used a quick `settings.TIME_ZONE` patch; this was rejected in favor of proper architecture (store UTC, convert to each viewer's local time at display time) to correctly support international teacher/student pairs — this matches Calendly/Acuity industry practice.
- `Booking.start_at`/`end_at` are aware `DateTimeField`s (was `date`/`start_time`/`end_time`)
- Booking submission takes a single `start_at` ISO string (with offset) instead of separate date+time fields
- Four `Booking` properties (`student_local_start/end`, `teacher_local_start/end`) handle correct per-viewer local-time display. **Gotcha:** Django's `|date`/`|time` template filters silently re-convert aware datetimes to `settings.TIME_ZONE` regardless of the datetime's own tzinfo — fixed by doing the timezone conversion in Python then calling `.replace(tzinfo=None)` so Django's filters can't re-convert it.
- **Rule:** which `_local_` property to use is determined by WHO IS VIEWING the page, not who the event "belongs to."
- RegularAvailability/WeeklyOverride stay naive on purpose (see data model section).

### 4. Session-based auth, not JWT (deliberate, revisited and reconfirmed post-CS50)
Considered switching to JWT for the "industrial phase" but decided against it: JWT's benefits (statelessness, multi-client support) don't apply to this monolith server-rendered architecture. Django's session auth already handles CSRF, real logout/session-invalidation, and is battle-tested. **Decision: keep session auth. Only add JWT later as a separate API layer (Django REST Framework + JWT) if/when a mobile app or public API is built — don't migrate the existing site's auth.**

### 5. Instant Tutoring toggle
Modeled on AmazingTalker: by default, students can only book from the teacher's "tomorrow" onward (teacher's timezone) — the teacher's entire current calendar day is hidden. Teacher can enable `TeacherProfile.instant_tutoring_enabled` to allow same-day bookings, subject to a **fixed 1-hour buffer** from now (not yet teacher-configurable — backlog item).

### 6. Deferred features (intentional CS50 scope decisions, now being revisited)
Payment integration and student↔teacher messaging were explicitly deferred to a later commercial phase — out of scope for the CS50 submission. Now part of the "industrial phase" backlog (see BACKLOG.md).

## Deployment architecture

### Why these choices
Chose a manual VPS + Docker Compose (over PaaS like Railway/Render) deliberately, for the Linux/Docker learning value (developer is job-hunting and wants these skills, not just the fastest path to a live site).

### Stack
- **VPS:** provisioned with a non-root `deployer` user (sudo + `docker` group membership, so Docker commands don't need sudo — required for CI automation), SSH key-only auth (`PermitRootLogin no`, `PasswordAuthentication no` in sshd_config)
- **Containers (`docker-compose.prod.yml`):**
  - `postgres` — Postgres 16-alpine, healthcheck via `pg_isready`, **no exposed host port** (only reachable inside the Docker network, for security)
  - `web` — Django + Gunicorn, built from a project `Dockerfile`, command runs `migrate` + `collectstatic` then launches Gunicorn on `0.0.0.0:8000` (only `expose`d internally, not published to the host — Nginx is the only thing that talks to it)
  - `autoheal` (willfarrell/autoheal image) — watches containers labeled `autoheal=true` and restarts them if Docker reports them `unhealthy` (a healthcheck alone only changes container *status*, it doesn't restart anything by itself — `autoheal` is what actually does the restart)
- **Static/media files:** `docker-compose.prod.yml` uses **bind mounts** (`./staticfiles:/app/staticfiles`, `./media:/app/media`), NOT named volumes. **Important history:** this was originally a named volume, which caused a production bug — Nginx (running on the host as `www-data`) couldn't read Docker's internally-managed volume storage (root-only permissions), causing uploaded teacher photos to 403. Fixed by switching to bind mounts pointing at a known host path (`~/app/staticfiles`, `~/app/media`), plus `chmod o+x` on `/home/deployer` and `~/app` (execute-only, not read — needed for Nginx to traverse into the bind-mount path without being able to list/browse the rest of the home directory).
- **Nginx** (installed directly on the VPS host, not containerized — deliberate choice: unnecessary complexity for a single-project server, would reconsider if multiple projects were hosted on the same box): reverse proxy to `127.0.0.1:8000`, serves `/static/` and `/media/` directly via `alias`, HTTPS via Let's Encrypt/Certbot (auto HTTP→HTTPS redirect for both apex and `www`)
- **Firewall:** `ufw`, default deny incoming / allow outgoing, only `22/tcp`, `80/tcp`, `443/tcp` open
- **Backups:** `~/backup_db.sh` — daily `pg_dump` from the `postgres` container, gzip-compressed, 7-day retention, scheduled via crontab
- **Docker log rotation:** configured daemon-wide in `/etc/docker/daemon.json` (`max-size: 50m`, `max-file: 5`) to prevent disk fill-up over time — applies to every container automatically

### CI/CD
- `.github/workflows/ci.yml` ("Django CI") — runs the test suite against Postgres on every push/PR to main/master
- `.github/workflows/deploy.yml` ("Deploy to Production") — triggered via `workflow_run` **after** `Django CI` completes successfully on `main` (not on raw push — this ensures a failing test suite never triggers a deploy). SSHes into the VPS (via `appleboy/ssh-action`, using a dedicated SSH keypair stored in GitHub Secrets — separate from the developer's personal key, so it can be revoked independently) and runs `git pull` + `docker compose -f docker-compose.prod.yml up -d --build`
- Deliberately NOT zero-downtime/blue-green — judged as over-engineering for a single-instance personal project; a few seconds of downtime per deploy is acceptable

### Health check
Added a lightweight `/health/` Django endpoint (returns plain `"OK"`, no DB query or template render — kept intentionally minimal so it only reflects "is the Django process alive," not broader app health) for the `web` service's Docker healthcheck.

## Git workflow / conventions
- Branch naming: `feature/<issue-number>-<description>`, `fix/...`, `refactor/...`, `ops/...` as fitting — always branched from an up-to-date `main` (`git checkout main && git pull origin main && git checkout -b ...`) to avoid divergence issues, especially important since GitHub is configured to **squash-merge** PRs (creates a new commit hash on `main`, so continuing work on the same pre-merge branch causes duplicate/diverged history)
- Commit messages: Conventional Commits (`feat:`, `fix:`, `ci:`, `ops:`, `docs:`, `refactor:`, `test:`)
- Do NOT include any "Co-Authored-By" trailer, Anthropic attribution, or sign-offs in git commit messages. All commits must strictly contain only the Conventional Commit title and description, authored solely by me.
- One GitHub Issue per unit of work, PR body includes `Closes #<number>` to auto-close on merge
- Gaps discovered mid-work get their own new issue rather than scope creep — exception: large multi-step refactors where intermediate states would break the project (e.g. the timezone overhaul) go on one branch as ordered commits with a single squash-merge PR at the end

## Known gotchas (things that have bitten this project before)
1. **Squash-merge + continuing on the same branch** causes history divergence — always branch fresh from `main`.
2. **Named Docker volumes are invisible to host-level Nginx** — anything Nginx needs to read directly must be a bind mount to a known host path.
3. **Pasting Django template code from chat tools** can introduce invisible/non-standard whitespace or corrupt `|`/`"` characters via smart-quote substitution — causes confusing parser errors (e.g. unbalanced `{% endif %}` that's actually fine). Fix: retype/collapse the affected line rather than debugging the template logic itself.
4. **Django's `|date`/`|time` template filters ignore a datetime's own tzinfo** and re-convert to `settings.TIME_ZONE` — must strip tzinfo after doing the correct conversion in Python if you need to bypass this.
5. **`full_clean()` runs on every `save()`, not just creation** — a "no past start_at" validation rule was attempted in `Booking.clean()` and reverted because it broke legitimate updates to already-past bookings (completing/marking-not-held) and test fixtures. That kind of "only valid at the moment of a specific user action" rule belongs in the view/service layer, not the model's permanent invariants.
6. **`get_available_start_times` is the single source of truth** for both what's *displayed* as available and what's *re-validated* server-side on `book_slot` — a past-slot / stale-booking bug (#97) was fixed by adding one filter there, closing both the display and validation paths at once.

## Current status
CS50W submission is complete (all milestones done, screencast recorded). The project is now in a post-submission "industrial phase" of hardening and expanding toward a real commercial product. See `BACKLOG.md` for the current prioritized list of bugs and planned features.

## Agent skills

### Issue tracker

GitHub issues via `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context repository layout. See `docs/agents/domain.md`.
