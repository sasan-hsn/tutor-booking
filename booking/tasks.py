import logging
import smtplib
from datetime import date as dt_date, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo
from celery import shared_task
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from portfolio.models import TeacherProfile
from .models import Booking, TeacherDailyDigestRecord
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


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_teacher_daily_digest_email_task(self, teacher_id: int, target_date_str: str, skip_empty: bool = True):
    """
    Send teacher daily schedule digest email for target_date.
    Idempotent: skips if TeacherDailyDigestRecord exists for (teacher, target_date).
    If no confirmed bookings exist and skip_empty is True, records SKIPPED_EMPTY and skips email.
    """
    target_date = dt_date.fromisoformat(target_date_str)

    try:
        with transaction.atomic():
            teacher = (
                TeacherProfile.objects.select_for_update()
                .select_related('user')
                .get(pk=teacher_id)
            )
            if TeacherDailyDigestRecord.objects.filter(teacher=teacher, target_date=target_date).exists():
                logger.info(
                    "Daily digest already recorded for teacher #%s on %s; skipping.",
                    teacher_id,
                    target_date,
                )
                return

            teacher_tz = ZoneInfo(teacher.user.timezone)
            day_start = datetime.combine(target_date, dt_time.min).replace(tzinfo=teacher_tz)
            day_end = datetime.combine(target_date + timedelta(days=1), dt_time.min).replace(tzinfo=teacher_tz)

            bookings = list(
                Booking.objects.filter(
                    teacher=teacher,
                    status=Booking.Status.CONFIRMED,
                    start_at__gte=day_start,
                    start_at__lt=day_end,
                )
                .select_related('student', 'teacher__user')
                .order_by('start_at')
            )

            if len(bookings) == 0 and skip_empty:
                logger.info(
                    "No confirmed bookings on %s for teacher #%s; skipping digest email and recording.",
                    target_date,
                    teacher_id,
                )
                TeacherDailyDigestRecord.objects.create(
                    teacher=teacher,
                    target_date=target_date,
                    booking_count=0,
                    status=TeacherDailyDigestRecord.Status.SKIPPED_EMPTY,
                )
                return

            try:
                emails.send_teacher_daily_digest_email(teacher, target_date, bookings)
                TeacherDailyDigestRecord.objects.create(
                    teacher=teacher,
                    target_date=target_date,
                    booking_count=len(bookings),
                    status=TeacherDailyDigestRecord.Status.SENT,
                )
            except TRANSIENT_EMAIL_ERRORS as exc:
                logger.exception(
                    "Transient error sending daily digest email for teacher #%s on %s. Retrying...",
                    teacher_id,
                    target_date,
                )
                raise self.retry(exc=exc)
    except TeacherProfile.DoesNotExist:
        logger.warning("TeacherProfile #%s not found; skipping daily digest task.", teacher_id)
        return


@shared_task
def send_daily_schedule_digests(now=None):
    """
    Periodic task running e.g. hourly to evaluate whether a teacher's local time matches
    the daily digest dispatch window (default 20:00 local time).
    If matched and no digest has been sent yet for tomorrow, enqueues digest email task.
    """
    if now is None:
        now = timezone.now()

    target_hour = getattr(settings, 'TEACHER_DAILY_DIGEST_HOUR', 20)
    teachers = TeacherProfile.objects.select_related('user').all()

    evaluated_count = 0
    dispatched_count = 0

    for teacher in teachers:
        evaluated_count += 1
        teacher_tz = ZoneInfo(teacher.user.timezone)
        teacher_local_now = timezone.localtime(now, teacher_tz)

        if teacher_local_now.hour != target_hour:
            continue

        target_date = teacher_local_now.date() + timedelta(days=1)

        if TeacherDailyDigestRecord.objects.filter(teacher=teacher, target_date=target_date).exists():
            continue

        send_teacher_daily_digest_email_task.delay(teacher.id, target_date.isoformat())
        dispatched_count += 1

    logger.info(
        "Daily schedule digest sweep completed: evaluated %s teachers, dispatched %s digests.",
        evaluated_count,
        dispatched_count,
    )
    return {
        'evaluated_teachers': evaluated_count,
        'dispatched_digests': dispatched_count,
    }




