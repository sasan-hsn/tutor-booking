# Backlog — Post-CS50 "Industrial Phase"

Status: fresh recovery/deployment bugs are already fixed (media/static 403 issue — see CLAUDE.md "Deployment architecture" section). Everything below is still open.

## 🐞 Bugs
- [x] No password reset/recovery flow — a user who forgets their password has no way to recover their account (#152)
- [x] Lesson request stays `pending` indefinitely, even after its scheduled time has passed (should auto-expire) (#154)
- [x] Student can submit duplicate trial requests while one is already pending (#150)

## 🎨 Unfinished features / UI polish
- [ ] Pagination for the Upcoming/Past sections in both dashboards
- [ ] Teacher-configurable Instant Tutoring buffer (currently hardcoded to 1 hour — see CLAUDE.md)
- [ ] Visual/styling pass on `profile_settings.html`
- [ ] Visual/styling pass on the Past Lessons section
- [ ] WeeklyOverride "mark whole day off" toggle (currently requires filling Start/End inputs)
- [ ] Undecided: does the teacher dashboard need its own Past Lessons section? (Currently student-only, from #100)
- [ ] Visual polish pass on the landing page (pricing card section + hero section flagged specifically)

## 🔒 Security & infrastructure (recommended priority: before new user-facing features)
- [ ] Email verification at signup
- [ ] Rate limiting on the login view (brute-force protection)
- [ ] Error monitoring in production (e.g. Sentry — free tier is enough at this scale)
- [ ] Actually test restoring from a `.sql.gz` backup file (an untested backup is a risk)

## 🚀 Major planned features (rough priority order)
- [ ] Celery + Redis background task infrastructure (prerequisite for email/notifications — sending email synchronously in the request/response cycle is bad practice)
- [ ] In-platform notifications
- [ ] Email (booking confirmations, reminders, password reset)
- [ ] Student↔teacher messaging (decide up front: real-time via Django Channels/WebSockets, or simple polling/refresh — this decision significantly affects implementation complexity)
- [ ] Legal pages (Terms of Service / Privacy Policy) — required before payments go live
- [ ] Payments/financial section (last, after everything above)

## 🔮 Architecture — low priority, only if actually needed
- [ ] JWT / separate API layer — only if/when a mobile app or public third-party API is built. Do NOT use this to replace the current session-based auth on the main site (see CLAUDE.md, Architecture Decisions #4, for the reasoning already worked through).

## Notes
- More items may be added as the developer continues reviewing the project.
- This list intentionally does not include CS50-submission items — those are all complete.
