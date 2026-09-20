import logging
import smtplib
from datetime import timedelta
from celery import shared_task
from django.db import models
from django.utils import timezone
from .models import Booking
from . import emails

logger = logging.getLogger(__name__)

TRANSIENT_EMAIL_ERRORS = (
    smtplib.SMTPException,
    ConnectionError,
    TimeoutError,
    OSError,
)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_booking_request_student_email_task(self, booking_id: int):
    """Send student confirmation email for a newly requested lesson."""
    try:
        booking = Booking.objects.select_related('student', 'teacher__user').get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.warning("Booking #%s not found; skipping student notification.", booking_id)
        return

    try:
        emails.send_booking_request_student_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        logger.exception(
            "Transient error sending student confirmation email for booking #%s. Retrying...",
            booking_id,
        )
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_booking_request_teacher_email_task(self, booking_id: int):
    """Send teacher alert email for a newly requested lesson."""
    try:
        booking = Booking.objects.select_related('student', 'teacher__user').get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.warning("Booking #%s not found; skipping teacher notification.", booking_id)
        return

    try:
        emails.send_booking_request_teacher_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        logger.exception(
            "Transient error sending teacher alert email for booking #%s. Retrying...",
            booking_id,
        )
        raise self.retry(exc=exc)


def send_booking_request_notifications(booking_id: int):
    """Convenience helper to enqueue both student and teacher request emails."""
    send_booking_request_student_email_task.delay(booking_id)
    send_booking_request_teacher_email_task.delay(booking_id)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_booking_confirmed_student_email_task(self, booking_id: int):
    """Send student confirmation email with meeting link and calendar invite for a confirmed lesson."""
    try:
        booking = Booking.objects.select_related('student', 'teacher__user').get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.warning("Booking #%s not found; skipping confirmed student notification.", booking_id)
        return

    try:
        emails.send_booking_confirmed_student_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        logger.exception(
            "Transient error sending student confirmation email for booking #%s. Retrying...",
            booking_id,
        )
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_booking_declined_student_email_task(self, booking_id: int):
    """Send student notification email when teacher declines a lesson request."""
    try:
        booking = Booking.objects.select_related('student', 'teacher__user').get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.warning("Booking #%s not found; skipping declined student notification.", booking_id)
        return

    try:
        emails.send_booking_declined_student_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        logger.exception(
            "Transient error sending student declined notification email for booking #%s. Retrying...",
            booking_id,
        )
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_booking_cancelled_student_email_task(self, booking_id: int):
    """Send student notification email when teacher cancels a confirmed lesson."""
    try:
        booking = Booking.objects.select_related('student', 'teacher__user').get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.warning("Booking #%s not found; skipping cancelled student notification.", booking_id)
        return

    try:
        emails.send_booking_cancelled_student_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        logger.exception(
            "Transient error sending student cancelled notification email for booking #%s. Retrying...",
            booking_id,
        )
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_cancellation_requested_teacher_email_task(self, booking_id: int):
    """Send teacher alert email when student requests lesson cancellation."""
    try:
        booking = Booking.objects.select_related('student', 'teacher__user').get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.warning("Booking #%s not found; skipping teacher cancellation request notification.", booking_id)
        return

    try:
        emails.send_cancellation_requested_teacher_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        logger.exception(
            "Transient error sending teacher cancellation request alert email for booking #%s. Retrying...",
            booking_id,
        )
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_booking_reminder_24h_student_email_task(self, booking_id: int):
    """Send student 24-hour reminder email for an upcoming confirmed lesson."""
    try:
        booking = Booking.objects.select_related('student', 'teacher__user').get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.warning("Booking #%s not found; skipping 24h reminder notification.", booking_id)
        return

    if booking.status != Booking.Status.CONFIRMED:
        logger.info(
            "Booking #%s is not confirmed (status=%s); skipping 24h reminder notification.",
            booking_id,
            booking.status,
        )
        return

    try:
        emails.send_booking_reminder_24h_student_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        logger.exception(
            "Transient error sending 24h reminder email for booking #%s. Retrying...",
            booking_id,
        )
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_booking_reminder_1h_student_email_task(self, booking_id: int):
    """Send student 1-hour reminder email for an upcoming confirmed lesson."""
    try:
        booking = Booking.objects.select_related('student', 'teacher__user').get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.warning("Booking #%s not found; skipping 1h student reminder notification.", booking_id)
        return

    if booking.status != Booking.Status.CONFIRMED:
        logger.info(
            "Booking #%s is not confirmed (status=%s); skipping 1h student reminder notification.",
            booking_id,
            booking.status,
        )
        return

    try:
        emails.send_booking_reminder_1h_student_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        logger.exception(
            "Transient error sending 1h student reminder email for booking #%s. Retrying...",
            booking_id,
        )
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_booking_reminder_1h_teacher_email_task(self, booking_id: int):
    """Send teacher 1-hour reminder email for an upcoming confirmed lesson."""
    try:
        booking = Booking.objects.select_related('student', 'teacher__user').get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.warning("Booking #%s not found; skipping 1h teacher reminder notification.", booking_id)
        return

    if booking.status != Booking.Status.CONFIRMED:
        logger.info(
            "Booking #%s is not confirmed (status=%s); skipping 1h teacher reminder notification.",
            booking_id,
            booking.status,
        )
        return

    try:
        emails.send_booking_reminder_1h_teacher_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        logger.exception(
            "Transient error sending 1h teacher reminder email for booking #%s. Retrying...",
            booking_id,
        )
        raise self.retry(exc=exc)


@shared_task
def send_lesson_reminders(now=None):
    """
    Periodic sweeper task scheduled every 5 minutes:
    - 24h sweep: queries confirmed bookings in [now + 23h, now + 25h] (bounded so bookings confirmed <24h away do not fire)
      where reminder_24h_sent is False and created_at <= start_at - 24h. Sends 24h reminder to student only.
    - 1h sweep: queries confirmed bookings where now < start_at <= now + 1h5m
      where reminder_1h_sent is False. Sends 1h reminder to both student and teacher.
    - Atomically updates tracking flags to prevent duplicate dispatch.
    """
    if now is None:
        now = timezone.now()

    # 1. 24-hour reminder sweep (Student only)
    window_24h_start = now + timedelta(hours=23)
    window_24h_end = now + timedelta(hours=25)

    # Automatically mark past or short-notice bookings that already missed the 24h window as sent/skipped
    Booking.objects.filter(
        status=Booking.Status.CONFIRMED,
        reminder_24h_sent=False,
        start_at__lt=window_24h_start,
    ).update(reminder_24h_sent=True)

    eligible_24h_ids = list(
        Booking.objects.filter(
            status=Booking.Status.CONFIRMED,
            reminder_24h_sent=False,
            start_at__gt=now,
            start_at__gte=window_24h_start,
            start_at__lte=window_24h_end,
            created_at__lte=models.F('start_at') - timedelta(hours=24),
        ).values_list('id', flat=True)
    )

    reminders_24h_count = 0
    for booking_id in eligible_24h_ids:
        updated = Booking.objects.filter(pk=booking_id, reminder_24h_sent=False).update(reminder_24h_sent=True)
        if updated:
            send_booking_reminder_24h_student_email_task.delay(booking_id)
            reminders_24h_count += 1

    # 2. 1-hour reminder sweep (Student and Teacher)
    window_1h_end = now + timedelta(hours=1, minutes=5)

    # Automatically mark past bookings as sent/skipped for 1h reminder
    Booking.objects.filter(
        status=Booking.Status.CONFIRMED,
        reminder_1h_sent=False,
        start_at__lte=now,
    ).update(reminder_1h_sent=True)

    eligible_1h_ids = list(
        Booking.objects.filter(
            status=Booking.Status.CONFIRMED,
            reminder_1h_sent=False,
            start_at__gt=now,
            start_at__lte=window_1h_end,
        ).values_list('id', flat=True)
    )

    reminders_1h_count = 0
    for booking_id in eligible_1h_ids:
        updated = Booking.objects.filter(pk=booking_id, reminder_1h_sent=False).update(reminder_1h_sent=True)
        if updated:
            send_booking_reminder_1h_student_email_task.delay(booking_id)
            send_booking_reminder_1h_teacher_email_task.delay(booking_id)
            reminders_1h_count += 1

    logger.info(
        "Lesson reminder sweep completed: %s 24h reminders, %s 1h reminders dispatched.",
        reminders_24h_count,
        reminders_1h_count,
    )
    return {
        'reminders_24h': reminders_24h_count,
        'reminders_1h': reminders_1h_count,
    }


@shared_task
def expire_pending_bookings_task():
    """Periodically sweep and expire stale pending booking requests."""
    from .services import expire_stale_bookings

    count = expire_stale_bookings()
    logger.info("Expired %s stale pending booking(s).", count)
    return count



