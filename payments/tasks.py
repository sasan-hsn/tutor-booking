import logging
import smtplib
from datetime import timedelta

import requests
from anymail.exceptions import AnymailAPIError
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from payments import emails
from payments.models import Order, PaymentAddress, Payout

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
        return getattr(exc, "status_code", None) in (429, 500, 502, 503, 504)
    if isinstance(exc, TRANSIENT_EMAIL_ERRORS):
        return True
    return False


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
    name="payments.tasks.send_receiving_address_change_alert_task",
)
def send_receiving_address_change_alert_task(self, payment_address_id: int) -> bool:
    """Async task to dispatch security alert email when a receiving address change is scheduled."""
    try:
        payment_address = PaymentAddress.objects.select_related(
            "tenant__teacher_profile__user"
        ).get(pk=payment_address_id)
    except PaymentAddress.DoesNotExist:
        logger.warning(
            "PaymentAddress #%s not found; skipping security alert email.",
            payment_address_id,
        )
        return False

    try:
        return emails.send_receiving_address_change_alert_email(payment_address)
    except TRANSIENT_EMAIL_ERRORS as exc:
        if isinstance(exc, AnymailAPIError) and not is_transient_email_error(exc):
            raise
        logger.exception(
            "Transient error sending receiving address security alert for #%s. Retrying...",
            payment_address_id,
        )
        raise self.retry(exc=exc)


def safe_send_receiving_address_change_alert(payment_address_id: int) -> None:
    """Safely enqueue receiving address security alert email without blocking HTTP responses."""
    try:
        send_receiving_address_change_alert_task.delay(payment_address_id)
    except Exception:
        logger.exception(
            "Failed to enqueue receiving address security alert task for #%s",
            payment_address_id,
        )


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


@shared_task(name="payments.tasks.activate_pending_tenant_accounts_task")
def activate_pending_tenant_accounts_task() -> int:
    """
    Hourly Celery Beat periodic task that promotes matured PENDING_ACTIVATION
    receiving addresses (activates_at <= now()) to ACTIVE.
    Retires any prior ACTIVE address for that (tenant, provider) *before*
    promoting the pending address inside transaction.atomic() with row locks
    to satisfy the unique_active_tenant_provider_address constraint.
    """
    from payments.services import lock_tenant_payment_addresses

    now = timezone.now()
    logger.info("Starting hourly pending receiving address activation sweep.")

    candidates = list(
        PaymentAddress.objects.filter(
            status=PaymentAddress.Status.PENDING_ACTIVATION,
            activates_at__lte=now,
        )
        .values_list("id", "tenant_id", "provider")
        .order_by("id")
    )

    activated_count = 0
    for pending_id, tenant_id, provider in candidates:
        with transaction.atomic():
            scoped_addresses = lock_tenant_payment_addresses(
                tenant_id=tenant_id,
                provider=provider,
            )
            pending_addr = next(
                (addr for addr in scoped_addresses if addr.id == pending_id),
                None,
            )
            if (
                pending_addr is None
                or pending_addr.status != PaymentAddress.Status.PENDING_ACTIVATION
                or (pending_addr.activates_at and pending_addr.activates_at > now)
            ):
                continue

            # Retire any existing ACTIVE address BEFORE promoting the pending one.
            for active_addr in scoped_addresses:
                if (
                    active_addr.id != pending_addr.id
                    and active_addr.status == PaymentAddress.Status.ACTIVE
                ):
                    active_addr.status = PaymentAddress.Status.RETIRED
                    active_addr.retired_at = now
                    active_addr.save(update_fields=["status", "retired_at"])

            pending_addr.status = PaymentAddress.Status.ACTIVE
            pending_addr.save(update_fields=["status"])
            activated_count += 1

            logger.info(
                "Activated receiving address: address_id=%s tenant_id=%s provider=%s",
                pending_addr.id,
                tenant_id,
                provider,
            )

    logger.info(
        "Pending receiving address activation sweep completed: %d addresses activated.",
        activated_count,
    )
    return activated_count
