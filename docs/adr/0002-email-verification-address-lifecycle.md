# 2. Email Verification and Address Lifecycle Architecture

Date: 2026-09-23

## Status

Accepted

## Context

As the platform transitions from a personal booking tool into a production service and lays the groundwork for a future multi-tenant tutor platform, email is the primary channel for booking requests, Google Meet links, lesson reminders, and password resets.

Unverified email addresses expose the platform to several failure modes:
1. Students mistyping their email address during registration and never receiving booking confirmations, lesson links, or reminder alerts.
2. Malicious signups submitting fake or arbitrary emails to spam third-party mailboxes or abuse transactional Brevo quotas.
3. Account takeovers or delivery disruption if users change their registered email address without proving ownership of the new address.
4. Premature verification cancellation or account lockouts triggered by aggressive anti-malware link pre-fetchers (e.g. Microsoft SafeLinks) that visit links on incoming emails via HTTP GET.

## Decision

We will implement email verification and a secure address lifecycle across both student and teacher accounts:

1. **Role Scope & Grandfathering**: Both students and teachers must verify their email addresses. Existing production accounts with non-empty emails are grandfathered as verified (`is_email_verified=True`) via a dedicated data migration, normalizing all existing addresses to lowercase.
2. **Soft-Gating UX**:
   - Students are logged in immediately upon registration and can explore the site and calendar without interruption. They are blocked only when confirming a booking (`book_slot`), returning a structured `403` with an inline resend prompt, accompanied by a persistent alert banner.
   - Teachers can configure their profile bio and pricing, but saving availability (`RegularAvailability`, `WeeklyOverride`) is blocked, and unverified teacher slots remain hidden from the public booking calendar.
3. **Stateless Signed Verification Tokens**: Tokens are generated using `django.core.signing.TimestampSigner` with a 24-hour TTL. Tokens are salted with the user's password hash (`user.password`), so any credential change or password reset immediately invalidates all outstanding verification tokens.
4. **Hybrid Pending Email Change Model**:
   - The active `user.email` remains functional, verified, and responsible for lesson notifications while an email change is in progress.
   - A `pending_email` field on the user model holds the unconfirmed address and is strictly reserved across all accounts to prevent race conditions and mailbox harassment.
   - Profile settings displays a clear `Pending confirmation: new@example.com [Resend] [Cancel]` state. Users can click `[Cancel]` at any time to immediately nullify `pending_email` and invalidate outstanding tokens.
5. **Security Advisory & Anti-Prefetch Defense**:
   - Initiating an email change dispatches a confirmation token to the new address and a security advisory to the old address.
   - The security advisory contains a one-click Revocation Link. To protect against automated corporate/webmail anti-malware scanners (e.g., SafeLinks) that pre-fetch links via `GET`, the revocation URL displays a confirmation screen on `GET` and executes the cancellation only upon `POST`.
6. **Token Resolution & Momentum Preservation**:
   - Clicking a verification link while logged out validates the token, auto-logins the user, and redirects them to a sanitized `next` URL (returning a booking student straight back to the calendar) or their role dashboard.
   - Clicking a link while logged in as a *different* user halts and prompts for logout to prevent session cross-contamination.
   - Successfully completing a password reset via email automatically marks the user's email as verified.
7. **Infrastructure & Rate Limiting**:
   - Dedicated Redis DB 1 (`redis://redis:6379/1`) is configured for Django's `CACHES` backend, isolating cache keys from Celery broker tasks on DB 0.
   - Verification resends are capped at a 60-second cooldown and a maximum of 5 requests per hour per user/IP.
   - Email dispatch tasks live in `accounts/tasks.py` with asynchronous Celery execution and exponential backoff on transient errors.

## Consequences

### Positive

- Seamless student onboarding momentum: registration is never blocked by inbox delays, yet lessons cannot be scheduled without a verified notification destination.
- Zero database maintenance for token tables via stateless cryptographic signing.
- Robust defense against email pre-fetching bots, token hijacking, and concurrent mailbox claims.
- Decoupled cache backend on Redis DB 1 prevents cache flush collisions with Celery queues.

### Negative

- Requires two additional columns on `accounts.User` (`is_email_verified` and `pending_email`).
- Requires configuring and maintaining a dedicated Redis cache backend alongside the Celery broker.
