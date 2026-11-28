import logging
import smtplib
from datetime import date as dt_date
from datetime import datetime
from datetime import time as dt_time
from datetime import timedelta
from zoneinfo import ZoneInfo

import requests
from anymail.exceptions import AnymailAPIError
from celery import shared_task
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from accounts.models import Tenant
from notifications.models import Notification
from portfolio.models import TeacherProfile
from wallet.services import settle_booking

from . import emails, notifications
from .models import Booking, TeacherDailyDigestRecord

logger = logging.getLogger(__name__)

TRANSIENT_EMAIL_ERRORS = (
    smtplib.SMTPException,
    ConnectionError,
    TimeoutError,
    OSError,
    requests.exceptions.RequestException,
    AnymailAPIError,
)


def is_transient_email_error(exc: Exception) -> bool:
    """Determine whether an email error is temporary and safe to retry."""
    if isinstance(exc, AnymailAPIError):
        # 429 Too Many Requests and 5xx Server Errors from Brevo API are transient
        return getattr(exc, "status_code", None) in (429, 500, 502, 503, 504)
    if isinstance(exc, TRANSIENT_EMAIL_ERRORS):
        return True
    return False


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_booking_request_student_email_task(self, booking_id: int):
    """Send student confirmation email for a newly requested lesson."""
    try:
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping student notification.", booking_id
        )
        return

    try:
        emails.send_booking_request_student_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        if isinstance(exc, AnymailAPIError) and not is_transient_email_error(exc):
            raise
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
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping teacher notification.", booking_id
        )
        return

    try:
        emails.send_booking_request_teacher_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        if isinstance(exc, AnymailAPIError) and not is_transient_email_error(exc):
            raise
        logger.exception(
            "Transient error sending teacher alert email for booking #%s. Retrying...",
            booking_id,
        )
        raise self.retry(exc=exc)


def send_booking_request_notifications(booking_id: int):
    """Safely enqueue both student and teacher request emails; never raise to the caller."""
    try:
        send_booking_request_student_email_task.delay(booking_id)
    except Exception:
        logger.exception(
            "Failed to enqueue student notification task for booking #%s", booking_id
        )

    try:
        send_booking_request_teacher_email_task.delay(booking_id)
    except Exception:
        logger.exception(
            "Failed to enqueue teacher notification task for booking #%s", booking_id
        )


def safe_send_booking_confirmed_email(booking_id: int):
    """Safely enqueue student confirmation email without raising to the caller."""
    try:
        send_booking_confirmed_student_email_task.delay(booking_id)
    except Exception:
        logger.exception(
            "Failed to enqueue confirmed student email task for booking #%s", booking_id
        )


def safe_send_booking_declined_email(booking_id: int):
    """Safely enqueue student decline email without raising to the caller."""
    try:
        send_booking_declined_student_email_task.delay(booking_id)
    except Exception:
        logger.exception(
            "Failed to enqueue declined student email task for booking #%s", booking_id
        )


def safe_send_booking_cancelled_email(booking_id: int):
    """Safely enqueue student cancellation email without raising to the caller."""
    try:
        send_booking_cancelled_student_email_task.delay(booking_id)
    except Exception:
        logger.exception(
            "Failed to enqueue cancelled student email task for booking #%s", booking_id
        )


def safe_send_cancellation_requested_email(booking_id: int):
    """Safely enqueue teacher cancellation requested email without raising to the caller."""
    try:
        send_cancellation_requested_teacher_email_task.delay(booking_id)
    except Exception:
        logger.exception(
            "Failed to enqueue cancellation requested teacher email task for booking #%s",
            booking_id,
        )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_booking_confirmed_student_email_task(self, booking_id: int):
    """Send student confirmation email with meeting link and calendar invite for a confirmed lesson."""
    try:
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping confirmed student notification.",
            booking_id,
        )
        return

    try:
        emails.send_booking_confirmed_student_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        if isinstance(exc, AnymailAPIError) and not is_transient_email_error(exc):
            raise
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
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping declined student notification.", booking_id
        )
        return

    try:
        emails.send_booking_declined_student_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        if isinstance(exc, AnymailAPIError) and not is_transient_email_error(exc):
            raise
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
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping cancelled student notification.",
            booking_id,
        )
        return

    try:
        emails.send_booking_cancelled_student_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        if isinstance(exc, AnymailAPIError) and not is_transient_email_error(exc):
            raise
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
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping teacher cancellation request notification.",
            booking_id,
        )
        return

    try:
        emails.send_cancellation_requested_teacher_email(booking)
    except TRANSIENT_EMAIL_ERRORS as exc:
        if isinstance(exc, AnymailAPIError) and not is_transient_email_error(exc):
            raise
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
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping 24h reminder notification.", booking_id
        )
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
        if isinstance(exc, AnymailAPIError) and not is_transient_email_error(exc):
            raise
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
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping 1h student reminder notification.",
            booking_id,
        )
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
        if isinstance(exc, AnymailAPIError) and not is_transient_email_error(exc):
            raise
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
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping 1h teacher reminder notification.",
            booking_id,
        )
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
        if isinstance(exc, AnymailAPIError) and not is_transient_email_error(exc):
            raise
        logger.exception(
            "Transient error sending 1h teacher reminder email for booking #%s. Retrying...",
            booking_id,
        )
        raise self.retry(exc=exc)


@shared_task
def send_booking_reminder_24h_student_notification_task(booking_id: int):
    """Send student 24-hour in-platform reminder notification for an upcoming confirmed lesson."""
    try:
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping 24h reminder notification.", booking_id
        )
        return

    if booking.status != Booking.Status.CONFIRMED:
        logger.info(
            "Booking #%s is not confirmed (status=%s); skipping 24h reminder notification.",
            booking_id,
            booking.status,
        )
        return

    with transaction.atomic():
        if Notification.objects.filter(
            recipient=booking.student,
            event_type=Notification.EventType.LESSON_REMINDER_24H,
            metadata__booking_id=booking.id,
        ).exists():
            logger.info(
                "24h reminder notification already exists for booking #%s", booking_id
            )
            return

        notifications.notify_lesson_reminder_24h_student(booking)


@shared_task
def send_booking_reminder_1h_student_notification_task(booking_id: int):
    """Send student 1-hour in-platform reminder notification for an upcoming confirmed lesson."""
    try:
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping 1h student reminder notification.",
            booking_id,
        )
        return

    if booking.status != Booking.Status.CONFIRMED:
        logger.info(
            "Booking #%s is not confirmed (status=%s); skipping 1h student reminder notification.",
            booking_id,
            booking.status,
        )
        return

    with transaction.atomic():
        if Notification.objects.filter(
            recipient=booking.student,
            event_type=Notification.EventType.LESSON_REMINDER_1H,
            metadata__booking_id=booking.id,
        ).exists():
            logger.info(
                "1h reminder notification already exists for student on booking #%s",
                booking_id,
            )
            return

        notifications.notify_lesson_reminder_1h_student(booking)


@shared_task
def send_booking_reminder_1h_teacher_notification_task(booking_id: int):
    """Send teacher 1-hour in-platform reminder notification for an upcoming confirmed lesson."""
    try:
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping 1h teacher reminder notification.",
            booking_id,
        )
        return

    if booking.status != Booking.Status.CONFIRMED:
        logger.info(
            "Booking #%s is not confirmed (status=%s); skipping 1h teacher reminder notification.",
            booking_id,
            booking.status,
        )
        return

    with transaction.atomic():
        if Notification.objects.filter(
            recipient=booking.teacher.user,
            event_type=Notification.EventType.LESSON_REMINDER_1H,
            metadata__booking_id=booking.id,
        ).exists():
            logger.info(
                "1h reminder notification already exists for teacher on booking #%s",
                booking_id,
            )
            return

        notifications.notify_lesson_reminder_1h_teacher(booking)


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
            created_at__lte=models.F("start_at") - timedelta(hours=24),
        ).values_list("id", flat=True)
    )

    reminders_24h_count = 0
    for booking_id in eligible_24h_ids:
        updated = Booking.objects.filter(pk=booking_id, reminder_24h_sent=False).update(
            reminder_24h_sent=True
        )
        if updated:
            send_booking_reminder_24h_student_email_task.delay(booking_id)
            send_booking_reminder_24h_student_notification_task.delay(booking_id)
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
        ).values_list("id", flat=True)
    )

    reminders_1h_count = 0
    for booking_id in eligible_1h_ids:
        updated = Booking.objects.filter(pk=booking_id, reminder_1h_sent=False).update(
            reminder_1h_sent=True
        )
        if updated:
            send_booking_reminder_1h_student_email_task.delay(booking_id)
            send_booking_reminder_1h_teacher_email_task.delay(booking_id)
            send_booking_reminder_1h_student_notification_task.delay(booking_id)
            send_booking_reminder_1h_teacher_notification_task.delay(booking_id)
            reminders_1h_count += 1

    logger.info(
        "Lesson reminder sweep completed: %s 24h reminders, %s 1h reminders dispatched.",
        reminders_24h_count,
        reminders_1h_count,
    )
    return {
        "reminders_24h": reminders_24h_count,
        "reminders_1h": reminders_1h_count,
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
def send_teacher_daily_digest_email_task(
    self, teacher_id: int, target_date_str: str, skip_empty: bool = True
):
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
                .select_related("user")
                .get(pk=teacher_id)
            )
            if TeacherDailyDigestRecord.objects.filter(
                teacher=teacher, target_date=target_date
            ).exists():
                logger.info(
                    "Daily digest already recorded for teacher #%s on %s; skipping.",
                    teacher_id,
                    target_date,
                )
                return

            teacher_tz = ZoneInfo(teacher.user.timezone)
            day_start = datetime.combine(target_date, dt_time.min).replace(
                tzinfo=teacher_tz
            )
            day_end = datetime.combine(
                target_date + timedelta(days=1), dt_time.min
            ).replace(tzinfo=teacher_tz)

            bookings = list(
                Booking.objects.filter(
                    teacher=teacher,
                    status=Booking.Status.CONFIRMED,
                    start_at__gte=day_start,
                    start_at__lt=day_end,
                )
                .select_related("student", "teacher__user")
                .order_by("start_at")
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
                if isinstance(exc, AnymailAPIError) and not is_transient_email_error(
                    exc
                ):
                    raise
                logger.exception(
                    "Transient error sending daily digest email for teacher #%s on %s. Retrying...",
                    teacher_id,
                    target_date,
                )
                raise self.retry(exc=exc)
    except TeacherProfile.DoesNotExist:
        logger.warning(
            "TeacherProfile #%s not found; skipping daily digest task.", teacher_id
        )
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

    target_hour = getattr(settings, "TEACHER_DAILY_DIGEST_HOUR", 20)
    teachers = TeacherProfile.objects.select_related("user").all()

    evaluated_count = 0
    dispatched_count = 0

    for teacher in teachers:
        evaluated_count += 1
        teacher_tz = ZoneInfo(teacher.user.timezone)
        teacher_local_now = timezone.localtime(now, teacher_tz)

        if teacher_local_now.hour != target_hour:
            continue

        target_date = teacher_local_now.date() + timedelta(days=1)

        if TeacherDailyDigestRecord.objects.filter(
            teacher=teacher, target_date=target_date
        ).exists():
            continue

        send_teacher_daily_digest_email_task.delay(teacher.id, target_date.isoformat())
        dispatched_count += 1

    logger.info(
        "Daily schedule digest sweep completed: evaluated %s teachers, dispatched %s digests.",
        evaluated_count,
        dispatched_count,
    )
    return {
        "evaluated_teachers": evaluated_count,
        "dispatched_digests": dispatched_count,
    }


def _settle_booking_batch(
    *,
    candidates: list[tuple[int, int]],
    is_eligible,
    action: str,
    reason: str,
    apply_post_settlement,
    task_label: str,
) -> int:
    """
    Process a list of (booking_id, teacher_id) candidates, each in its own atomic
    transaction under TeacherProfile -> Booking -> Wallet lock order, re-checking
    eligibility after acquiring row locks so one failure never rolls back the batch.
    """
    settled_count = 0
    for booking_id, teacher_id in candidates:
        try:
            with transaction.atomic():
                TeacherProfile.objects.select_for_update().get(pk=teacher_id)
                booking = (
                    Booking.objects.select_for_update().filter(pk=booking_id).first()
                )
                if booking is None or not is_eligible(booking):
                    continue

                tenant = Tenant.objects.filter(teacher_profile_id=teacher_id).first()
                if tenant is None:
                    continue

                settle_booking(
                    tenant=tenant,
                    booking=booking,
                    action=action,
                    actor=None,
                    reason=reason,
                )
                apply_post_settlement(booking)
                settled_count += 1
        except Exception:
            logger.exception(
                "Failed to %s booking #%s during hourly sweep.",
                task_label,
                booking_id,
            )
    return settled_count


@shared_task(name="booking.tasks.auto_complete_lessons_task")
def auto_complete_lessons_task(now=None) -> int:
    """
    Hourly Celery Beat task that sweeps CONFIRMED, RESERVED bookings whose end_at
    has passed AUTO_COMPLETE_HOURS (48h) without an open dispute, consuming the hold
    and marking the booking COMPLETED.
    """
    if now is None:
        now = timezone.now()

    auto_complete_hours = getattr(settings, "AUTO_COMPLETE_HOURS", 48)
    cutoff = now - timedelta(hours=auto_complete_hours)

    candidates = list(
        Booking.objects.filter(
            status=Booking.Status.CONFIRMED,
            payment_status=Booking.PaymentStatus.RESERVED,
            cancellation_requested=False,
            end_at__lte=cutoff,
        )
        .exclude(status=Booking.Status.DISPUTING)
        .order_by("id")
        .values_list("id", "teacher_id")
    )

    def _is_eligible(booking: Booking) -> bool:
        return (
            booking.status == Booking.Status.CONFIRMED
            and booking.payment_status == Booking.PaymentStatus.RESERVED
            and not booking.cancellation_requested
            and booking.end_at <= cutoff
        )

    def _mark_completed(booking: Booking) -> None:
        booking.status = Booking.Status.COMPLETED
        booking.save(update_fields=["status", "updated_at"])

    completed_count = _settle_booking_batch(
        candidates=candidates,
        is_eligible=_is_eligible,
        action="consume",
        reason=Booking.SettlementReason.COMPLETED,
        apply_post_settlement=_mark_completed,
        task_label="auto-complete",
    )

    logger.info(
        "Auto-complete lessons sweep completed: %d booking(s) completed.",
        completed_count,
    )
    return completed_count


@shared_task(name="booking.tasks.expire_disputes_task")
def expire_disputes_task(now=None) -> int:
    """
    Hourly Celery Beat task that sweeps DISPUTING, RESERVED bookings whose
    dispute_opened_at has passed DISPUTE_WINDOW_DAYS (7 days), releasing held funds
    back to the student wallet and marking the booking CANCELLED.
    """
    if now is None:
        now = timezone.now()

    dispute_window_days = getattr(settings, "DISPUTE_WINDOW_DAYS", 7)
    cutoff = now - timedelta(days=dispute_window_days)

    candidates = list(
        Booking.objects.filter(
            status=Booking.Status.DISPUTING,
            payment_status=Booking.PaymentStatus.RESERVED,
            dispute_opened_at__lte=cutoff,
        )
        .order_by("id")
        .values_list("id", "teacher_id")
    )

    def _is_eligible(booking: Booking) -> bool:
        return (
            booking.status == Booking.Status.DISPUTING
            and booking.payment_status == Booking.PaymentStatus.RESERVED
            and booking.dispute_opened_at is not None
            and booking.dispute_opened_at <= cutoff
        )

    def _mark_expired_auto_student(booking: Booking) -> None:
        booking.status = Booking.Status.CANCELLED
        booking.dispute_resolution = Booking.DisputeResolution.EXPIRED_AUTO_STUDENT
        booking.save(update_fields=["status", "dispute_resolution", "updated_at"])
        notifications.notify_dispute_resolved(
            booking,
            resolution=Booking.DisputeResolution.EXPIRED_AUTO_STUDENT,
        )
        transaction.on_commit(
            lambda bid=booking.id: safe_send_dispute_resolved_emails(
                bid, Booking.DisputeResolution.EXPIRED_AUTO_STUDENT
            )
        )

    expired_count = _settle_booking_batch(
        candidates=candidates,
        is_eligible=_is_eligible,
        action="release",
        reason=Booking.SettlementReason.DISPUTE_EXPIRED,
        apply_post_settlement=_mark_expired_auto_student,
        task_label="expire dispute for",
    )

    logger.info(
        "Expire disputes sweep completed: %d dispute(s) auto-expired in student favor.",
        expired_count,
    )
    return expired_count


def _execute_booking_email_task(task, send_fn, label: str, booking_id: int):
    try:
        return send_fn()
    except TRANSIENT_EMAIL_ERRORS as exc:
        if isinstance(exc, AnymailAPIError) and not is_transient_email_error(exc):
            raise
        logger.exception(
            "Transient error sending %s for booking #%s. Retrying...",
            label,
            booking_id,
        )
        raise task.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
    name="booking.tasks.send_dispute_opened_email_task",
)
def send_dispute_opened_email_task(self, booking_id: int) -> bool:
    """Async task to dispatch teacher alert email when a student opens a dispute."""
    try:
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping dispute opened email.",
            booking_id,
        )
        return False

    return _execute_booking_email_task(
        self,
        lambda: emails.send_dispute_opened_teacher_email(booking),
        "dispute opened email",
        booking_id,
    )


def safe_send_dispute_opened_email(booking_id: int) -> None:
    """Safely enqueue dispute opened email without blocking HTTP responses."""
    try:
        send_dispute_opened_email_task.delay(booking_id)
    except Exception:
        logger.exception(
            "Failed to enqueue dispute opened email task for booking #%s",
            booking_id,
        )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
    name="booking.tasks.send_dispute_resolved_emails_task",
)
def send_dispute_resolved_emails_task(self, booking_id: int, resolution: str) -> int:
    """Async task to dispatch dispute resolution emails to student and teacher."""
    try:
        booking = Booking.objects.select_related("student", "teacher__user").get(
            pk=booking_id
        )
    except Booking.DoesNotExist:
        logger.warning(
            "Booking #%s not found; skipping dispute resolved emails.",
            booking_id,
        )
        return 0

    return _execute_booking_email_task(
        self,
        lambda: emails.send_dispute_resolved_emails(booking, resolution=resolution),
        "dispute resolved emails",
        booking_id,
    )


def safe_send_dispute_resolved_emails(booking_id: int, resolution: str) -> None:
    """Safely enqueue dispute resolved emails without blocking HTTP responses."""
    try:
        send_dispute_resolved_emails_task.delay(booking_id, resolution)
    except Exception:
        logger.exception(
            "Failed to enqueue dispute resolved emails task for booking #%s",
            booking_id,
        )


@shared_task(name="booking.tasks.dispute_warning_task")
def dispute_warning_task(now=None) -> int:
    """
    Daily Celery Beat task that detects open disputes reaching Day 3
    (3 days <= age < 4 days) and Day 6 (6 days <= age < 7 days), sending
    urgent warning emails to the teacher before automatic student refund.
    """
    if now is None:
        now = timezone.now()

    dispute_window_days = getattr(settings, "DISPUTE_WINDOW_DAYS", 7)
    sent_count = 0

    for warning_day in (3, 6):
        window_end = now - timedelta(days=warning_day)
        window_start = now - timedelta(days=warning_day + 1)
        days_remaining = max(dispute_window_days - warning_day, 1)

        disputes = (
            Booking.objects.filter(
                status=Booking.Status.DISPUTING,
                payment_status=Booking.PaymentStatus.RESERVED,
                dispute_opened_at__lte=window_end,
                dispute_opened_at__gt=window_start,
            )
            .select_related("student", "teacher__user")
            .order_by("id")
        )

        for booking in disputes:
            try:
                dispatched = emails.send_dispute_warning_teacher_email(
                    booking,
                    warning_day=warning_day,
                    days_remaining=days_remaining,
                )
                if dispatched:
                    sent_count += 1
            except Exception:
                logger.exception(
                    "Failed to send Day %s dispute warning email for booking #%s.",
                    warning_day,
                    booking.id,
                )

    logger.info(
        "Dispute warning sweep completed: %d urgent warning email(s) sent.",
        sent_count,
    )
    return sent_count
