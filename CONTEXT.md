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
