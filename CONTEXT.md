# Tutor Booking

Online booking and management platform for independent 1-on-1 language tutoring.

## Language

**Booking**:
A scheduled lesson session between a student and a teacher with a defined start and end time.
_Avoid_: Appointment, session, reservation

**Lesson Request (Pending Booking)**:
A booking created by a student that awaits teacher review to be accepted or declined.
_Avoid_: Pending reservation, appointment inquiry

**Confirmed Booking**:
A booking accepted by the teacher that is scheduled to take place.
_Avoid_: Approved lesson, booked appointment

**Expired Booking**:
A pending booking whose scheduled start time has passed without the teacher accepting or declining it.
_Avoid_: Missed booking, abandoned request

**Cancelled Booking**:
A booking that was explicitly declined by the teacher, cancelled via mutual agreement, or marked as not held after completion time.
_Avoid_: Voided booking, deleted lesson

**Completed Booking**:
A confirmed booking that has taken place and was marked as completed by the teacher, unlocking the ability for the student to leave a review.
_Avoid_: Finished lesson, done booking

**Awaiting Resolution**:
The state of a confirmed booking after its scheduled end time has passed, during which the teacher must mark it as completed or not held.
_Avoid_: Unresolved lesson, pending completion

**Trial Lesson**:
An introductory, discounted or free lesson offered to first-time students by a teacher.
_Avoid_: Demo lesson, sample class

**Regular Lesson**:
A standard-priced lesson booked at the teacher's default rate and duration.
_Avoid_: Standard lesson, paid lesson

**Lesson Reminder**:
An automated notification sent to a student and teacher prior to a confirmed booking's scheduled start time.
_Avoid_: Lesson alert, booking notification, calendar ping

**Meeting Link**:
A video conference URL (e.g., Google Meet, Zoom) associated with a teacher where the online lesson takes place.
_Avoid_: Classroom URL, video room, lesson link

**Verified User**:
An account whose email address has been confirmed through an email verification link.
_Avoid_: Activated account, confirmed user

**Unverified User**:
An account whose registered email address has not yet been confirmed via verification link.
_Avoid_: Inactive account, unconfirmed user

**Pending Email Change**:
An in-progress transition where a user requests to change their registered email address; the current address remains active and verified until the new address is confirmed via verification link.
_Avoid_: Unconfirmed email swap, email update request

**Verification Token**:
A time-limited, cryptographically signed token used to prove ownership of an email address.
_Avoid_: Activation code, confirmation hash, verification key

**Revocation Link**:
A one-click security URL included in advisory emails that allows an account holder to immediately terminate an unauthorized pending email change.
_Avoid_: Undo link, cancellation button

**Login Lockout**:
A temporary security block on authentication attempts for a specific (IP address, account) or client after repeated failed login attempts.
_Avoid_: Account ban, account suspension, IP blacklist

**Rate-Limited Client**:
A client IP or user currently restricted from performing sensitive actions (such as logging in or requesting verification emails) due to exceeding request frequency thresholds.
_Avoid_: Blocked client, throttled user, banned visitor

**Global IP Ceiling**:
A sliding-window rate limit restricting total failed authentication attempts originating from a single IP address across all usernames to mitigate horizontal password spraying and distributed credential stuffing.
_Avoid_: IP blacklist, IP ban, server firewall block


