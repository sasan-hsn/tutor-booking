# Feature Specification: Email Verification and Address Lifecycle

## Problem Statement

When students and teachers create accounts or update their contact information, typos or fake addresses can enter the system undetected. Because all lesson requests, confirmations, Google Meet classroom links, cancellation alerts, and 24-hour/1-hour lesson reminders are delivered exclusively via email, an invalid or unverified address causes students to miss scheduled lessons, leaves teachers wondering why students failed to attend, and wastes transactional email quotas. Furthermore, if an account holder updates their email address without proving control of the new inbox, or if an unauthorized party changes an account's email without notifying the original address, the account can be taken over or communications abruptly severed.

## Solution

A robust, soft-gated email verification and address lifecycle system that:
1. Verifies ownership of registered email addresses upon signup for both students and teachers, while preserving onboarding momentum by allowing full platform exploration until lesson booking or availability publishing.
2. Implements a secure pending email change workflow where the existing verified address remains active until the new inbox confirms ownership, accompanied by an instant one-click revocation link sent to the old address.
3. Defends against email-scanning bot pre-fetches, invalidates outstanding tokens upon credential changes, enforces case-insensitive email uniqueness, and protects mail delivery quotas with Redis-backed rate limiting.

## User Stories

1. As a new student, I want to explore the teacher's profile and calendar immediately after signing up, so that I don't lose onboarding momentum waiting for an email.
2. As a new student, I want to receive an email verification link after registration, so that I can prove ownership of my email address.
3. As an unverified student, I want to see a clear notification banner across my dashboard and booking views, so that I know email verification is required before I can book a lesson.
4. As an unverified student, I want to be able to click a "Resend verification email" button from the banner or booking modal, so that I can get a fresh link if the original was lost or expired.
5. As an unverified student, I want to receive a clear explanation if I attempt to confirm a lesson booking before verifying my email, so that I understand why the booking cannot proceed.
6. As a student, I want clicking the verification link in my email to automatically sign me in and return me directly to the booking calendar, so that I can immediately complete the lesson booking I started.
7. As a student, I want clicking a verification link in an external browser where another user is logged in to prompt me to log out first, so that I do not accidentally mix user sessions.
8. As a new teacher, I want to draft my profile, bio, and pricing while unverified, so that I can prepare my teaching presence without delay.
9. As an unverified teacher, I want the system to block me from saving availability or publishing open slots to the public calendar, so that students cannot book times with an unconfirmed contact channel.
10. As a verified student or teacher, I want to see a green "Verified" badge next to my email in profile settings, so that I have visual confirmation of my account's verified status.
11. As a verified user, I want to initiate an email address change from my profile settings, so that I can transition to a new email address when needed.
12. As a user who initiated an email change, I want my active email address to remain fully functional for lesson notifications and login until I confirm the new address, so that ongoing lessons are not disrupted.
13. As a user with an in-progress email change, I want to see a "Pending confirmation: new@example.com" notice in my profile settings with options to resend the link or cancel the request, so that I have full control over the change.
14. As a user with an in-progress email change, I want to be able to cancel the request at any time, so that typos or mistaken changes can be undone immediately.
15. As an account owner, I want to receive a security advisory email at my original address whenever an email change is initiated, so that I am immediately alerted if someone attempts an unauthorized change.
16. As an account owner who received an unauthorized email change advisory, I want to click a revocation link to open a confirmation page where I can immediately cancel the change, so that automated security scanners do not trigger accidental cancellations while I retain one-click protection.
17. As an unverified user who forgot their password, I want to be able to reset my password via email and have my email automatically marked as verified upon successful reset, so that I am not forced to perform redundant verification steps.
18. As a platform administrator, I want email addresses to be strictly unique across accounts in a case-insensitive manner, so that account collisions and ambiguous notification deliveries are eliminated.
19. As a platform administrator, I want pending email addresses to be reserved across the system while unconfirmed, so that malicious actors cannot flood a victim's inbox with duplicate confirmation requests.
20. As a platform administrator, I want verification link requests to be rate-limited by user and IP address, so that transactional email quotas are protected from abuse and spam.
21. As an existing production user, I want my account to be grandfathered as verified during system migration, so that existing teachers and students experience no unexpected service interruption.

## Implementation Decisions

### Domain & Data Model
- Add `is_email_verified` (boolean, default False) and `pending_email` (nullable email string) to the custom user model.
- Enforce case-insensitive email uniqueness across all users at both the database level (case-insensitive unique index or constraint on lowercase email) and within form clean validation. Normalize all stored emails to lowercase.
- Enforce strict reservation of `pending_email`: an email address currently recorded as a pending change on any account cannot be claimed as a login email or another pending change by any other account.

### Token Architecture & Cryptographic Security
- Verification tokens are generated statelessly using cryptographic timestamped signing with a 24-hour expiration window.
- The cryptographic signature is salted with the user's password hash (`user.password`). Any password change, credential reset, or account compromise immediately invalidates all outstanding verification tokens.
- For initial signups, the token payload binds the user identity and the email address being verified.
- For email changes, the token payload binds the user identity and the target new email address. A token is only accepted if the target email matches the current `pending_email` in the database, ensuring superseded requests are dead upon replacement.
- For revocation, a distinct cryptographic action signature is used, bound to the user identity and the pending email.

### User Experience & Gating Boundaries
- **Student Soft-Gate**: Registration logs the student in and redirects to their dashboard. A persistent top banner indicates unverified status. In the booking flow, the calendar and slots are fully interactive; clicking confirm on a slot returns an HTTP 403 response with an `email_unverified` payload, opening an inline prompt in the booking modal with a resend button.
- **Teacher Availability Gate**: Unverified teachers can edit profile details, but any attempt to save regular availability or weekly overrides is rejected with an explanatory message. Slots for unverified teachers are omitted from the public booking day-picker.
- **Email Change UI**: Profile settings forms display the current email with its verified status badge. When an email change is initiated, the input reflects the active email while a dedicated sub-panel shows the pending address, a resend action, and a cancel button.
- **Revocation Safety (Anti-Prefetch Protection)**: The revocation link renders a GET confirmation page with an explicit POST action button, complying with RFC 7231 safe method standards and preventing corporate anti-malware scanners (e.g. Microsoft SafeLinks) from canceling legitimate requests.
- **Login Session Handshake**:
  - Clicking a verification link while logged out validates the token, logs the user in, and redirects to a safe `next` parameter (or role dashboard).
  - Clicking a link while logged in as a different user displays an interception page prompting them to log out before proceeding.
  - Completing an email-based password reset automatically marks `is_email_verified = True`.

### Infrastructure & Rate Limiting
- Configure a dedicated Redis cache backend database (database index 1) isolated from the Celery message broker (database index 0) to avoid cache flush collisions.
- Throttle verification email resend requests to a 60-second cooldown per user/IP and an hourly maximum of 5 requests using the cache backend.
- Asynchronous email tasks are executed via Celery with automated retries and exponential backoff on transient delivery failures.
- Unified responsive email layouts: promote the base email container to a shared global email template maintaining consistent typography and branding across all notifications.

## Testing Decisions

### What Makes a Good Test
- Tests must verify external system behavior through HTTP requests, response status codes, template rendering, and database state transitions, rather than inspecting internal helper methods.
- Tests must assert that emails arrive in Django's test mail outbox with correct recipients, subjects, and signed URLs.
- Tests must verify security invariants: rejecting expired tokens, rejecting tampered tokens, rejecting tokens after password changes, and blocking anti-scanner GET revocations.

### Modules & Flows Tested
- **Signup & Verification**: Registration creates unverified user; verification email is queued; valid token GET verifies user, logs them in, and redirects to destination; tampered or expired token fails gracefully.
- **Booking Soft-Gate**: Unverified student is blocked from booking with 403; verified student books successfully; calendar renders unverified warning banner.
- **Teacher Availability Gate**: Unverified teacher cannot save availability; public day picker hides slots for unverified teachers.
- **Email Change Lifecycle**: Requesting new email sets pending state; sends confirmation to new email and alert to old email; canceling clears pending state; confirming swaps email and verifies account; revocation link cancels change via POST; competing claims on pending email are rejected.
- **Rate Limiting**: Rapid consecutive resend requests hit cooldown and hourly rate limit responses.
- **Password Reset Integration**: Password reset completion verifies unverified user.

### Prior Art
- `accounts/tests/test_views.py`: Authentication, signup, and login redirection test patterns.
- `booking/tests/test_access_control.py` & `test_book_slot.py`: Role-based access control and booking validation seams.
- `booking/tests/test_notifications.py`: Outbox inspection and Celery asynchronous task testing patterns.

## Out of Scope

- Multi-factor authentication (MFA / 2FA).
- Social login / OAuth providers (Google Sign-In, etc.).
- SMS / Phone number verification.
- Enforcing password re-authentication before requesting an email change.

## Further Notes

- A data migration will grandfather existing production accounts with non-empty emails as `is_email_verified=True` and normalize emails to lowercase.
- Architectural decisions are formally documented in ADR 0002.
