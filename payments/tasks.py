import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from payments.models import Order, Payout

logger = logging.getLogger(__name__)


@shared_task(name="payments.tasks.expire_payout_tokens_task")
def expire_payout_tokens_task() -> int:
    """
    Hourly Celery Beat periodic task that sweeps unconfirmed payouts in PENDING_CONFIRMATION status
    whose confirmation token has expired (confirmation_expires_at <= now()).
    Transitions them to EXPIRED.
    Returns the count of expired payouts.
    """
    now = timezone.now()
    logger.info("Starting hourly payout token expiration task sweep.")

    with transaction.atomic():
        expired_count = Payout.objects.filter(
            status=Payout.Status.PENDING_CONFIRMATION,
            confirmation_expires_at__lte=now,
        ).update(status=Payout.Status.EXPIRED)

    logger.info(
        "Payout token expiration sweep completed: %d payouts transitioned to EXPIRED.",
        expired_count,
    )
    return expired_count


@shared_task(name="payments.tasks.expire_orders_task")
def expire_orders_task() -> int:
    """
    Hourly Celery Beat periodic task that sweeps pending orders whose expiry deadline has passed
    (expires_at <= now()).
    Transitions them to EXPIRED.
    Returns the count of expired orders.
    """
    now = timezone.now()
    logger.info("Starting hourly order expiration task sweep.")

    with transaction.atomic():
        expired_count = Order.objects.filter(
            status=Order.Status.PENDING,
            expires_at__lte=now,
        ).update(status=Order.Status.EXPIRED)

    logger.info(
        "Order expiration sweep completed: %d orders transitioned to EXPIRED.",
        expired_count,
    )
    return expired_count


@shared_task(name="payments.tasks.payout_reminder_task")
def payout_reminder_task() -> int:
    """
    Periodic Celery Beat task running every 6 hours.
    Queries payouts in REQUESTED status where:
      confirmation_used_at <= now() - timedelta(hours=settings.PAYOUT_REMINDER_HOURS)
      and reminder_48h_sent_at__isnull=True.
    Sends reminder email to the tenant's teacher and marks reminder_48h_sent_at = now().
    Returns the count of reminder alerts sent.
    """
    now = timezone.now()
    reminder_hours = getattr(settings, "PAYOUT_REMINDER_HOURS", 48)
    cutoff = now - timedelta(hours=reminder_hours)
    logger.info("Starting payout reminder task sweep (cutoff=%s).", cutoff)

    payouts = (
        Payout.objects.filter(
            status=Payout.Status.REQUESTED,
            confirmation_used_at__lte=cutoff,
            reminder_48h_sent_at__isnull=True,
        )
        .select_related("tenant__teacher_profile__user", "student")
        .order_by("id")
    )

    reminded_count = 0
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tutorbooking.local")

    for payout in payouts:
        teacher_user = payout.tenant.teacher_profile.user
        student_name = payout.student.get_full_name() or payout.student.username
        subject = f"[Action Required] Payout Request Reminder: Payout #{payout.id}"
        message = (
            f"Hello {teacher_user.first_name or teacher_user.username},\n\n"
            f"This is a reminder that payout request #{payout.id} submitted by {student_name} "
            f"for {payout.amount_gross} {payout.tenant.default_currency} has been awaiting your review "
            f"for over {reminder_hours} hours.\n\n"
            f"Destination: {payout.destination}\n"
            f"Provider: {payout.provider}\n\n"
            f"Please review and process or reject this payout in your Teacher Dashboard Finance tab."
        )

        should_send = False
        with transaction.atomic():
            locked_payout = Payout.objects.select_for_update().get(pk=payout.pk)
            if (
                locked_payout.status == Payout.Status.REQUESTED
                and locked_payout.reminder_48h_sent_at is None
            ):
                locked_payout.reminder_48h_sent_at = now
                locked_payout.save(update_fields=["reminder_48h_sent_at"])
                should_send = True

        if should_send:
            send_mail(
                subject=subject,
                message=message,
                from_email=from_email,
                recipient_list=[teacher_user.email],
                fail_silently=False,
            )
            reminded_count += 1

            logger.info(
                "Payout reminder sent: payout_id=%s teacher_id=%s",
                payout.id,
                teacher_user.id,
            )

    logger.info(
        "Payout reminder task sweep completed: %d reminders sent.",
        reminded_count,
    )
    return reminded_count
