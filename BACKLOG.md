# Backlog — Post-CS50 "Industrial Phase"

Status: fresh recovery/deployment bugs are already fixed (media/static 403 issue — see CLAUDE.md "Deployment architecture" section). Everything below is still open.

## 🐞 Bugs
- [x] No password reset/recovery flow — a user who forgets their password has no way to recover their account (#152)
- [x] Lesson request stays `pending` indefinitely, even after its scheduled time has passed (should auto-expire) (#154)
- [x] Student can submit duplicate trial requests while one is already pending (#150)

## 🎨 Unfinished features / UI polish
- [x] "Join Lesson" direct meeting access on lesson cards for teacher and student (activates during the Lesson Join Window, 10 minutes before start through 10 minutes after end of lesson, linking directly to the teacher's meeting link) (#221)
- [x] Pagination for the Upcoming/Past sections in both dashboards (#207, #208, #209)
- [ ] Teacher-configurable Instant Tutoring buffer (currently hardcoded to 1 hour — see CLAUDE.md)
- [ ] Visual/styling pass on `profile_settings.html`
- [ ] Visual/styling pass on the Past Lessons section
- [ ] WeeklyOverride "mark whole day off" toggle (currently requires filling Start/End inputs)
- [x] Teacher dashboard Past Lessons section (#207, #209)
- [ ] Visual polish pass on the landing page (pricing card section + hero section flagged specifically)

## 🔒 Security & infrastructure (recommended priority: before new user-facing features)
- [x] Email verification & address lifecycle (#181, #182, #183, #184, #185, #186)
  - [x] Email verification at signup (asynchronous token via Celery, soft-gate booking submission & teacher publishing gate) (#182, #184)
  - [x] Allow email change in profile settings (verify new email via confirmation link before swapping, safe revocation link) (#185)
  - [x] Auto-verify email upon successful password reset (#186)
- [x] Rate limiting on the login view (brute-force protection) (#192, #193, #194, #195)
  - [x] Non-field error alert banner on login page (#193)
  - [x] Compound (IP, username) login rate limiting & pre-auth short-circuit (#194)
  - [x] Global IP ceiling on login & structured security audit logging (#195)
- [x] Error monitoring in production (e.g. Sentry — free tier is enough at this scale) (#199)
- [x] Actually test restoring from a `.sql.gz` backup file (an untested backup is a risk) (#201, #202, #203, #204, #205)

## 🚀 Major planned features (rough priority order)
- [x] Celery + Redis background task infrastructure & automated email notifications (Booking confirmations, reminders, cancellations, auto-expire sweep, teacher daily digest, Brevo HTTP API integration, resilient broker dispatch) (#160, #178, #180)
- [ ] In-platform notifications & dashboard alerts — notify teacher in real time (e.g. via polling or toast/badge) when new lesson or cancellation requests arrive without requiring manual page refresh (Transactional email via Brevo currently covers baseline alerts)
- [ ] Student↔teacher messaging (decide up front: real-time via Django Channels/WebSockets, or simple polling/refresh — this decision significantly affects implementation complexity)
- [ ] Legal pages (Terms of Service / Privacy Policy) — required before payments go live
- [ ] Payments/financial section (last, after everything above)

## 🔮 Architecture & Integrations — future / low priority
- [ ] Direct Google Meet API integration — automatically generate and attach unique Google Meet room links per booking (replaces static teacher meeting link)
- [ ] Social Auth (Google Sign-In / OAuth) — deferred to avoid unnecessary auth complexity, collision edge cases, and onboarding friction at current stage
- [ ] JWT / separate API layer — only if/when a mobile app or public third-party API is built. Do NOT use this to replace the current session-based auth on the main site (see CLAUDE.md, Architecture Decisions #4, for the reasoning already worked through).

## Notes
- More items may be added as the developer continues reviewing the project.
- This list intentionally does not include CS50-submission items — those are all complete.
