import logging
import smtplib
from celery import shared_task
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


