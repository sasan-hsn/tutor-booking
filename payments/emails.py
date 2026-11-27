import logging
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


def _send_dedup_teacher_email(
    *,
    teacher_user,
    dedup_key: str,
    subject: str,
    text_template: str,
    html_template: str,
    extra_context: dict,
    header_subtitle: str,
    headers: dict | None = None,
) -> bool:
    """Send a deduplicated multipart email to a verified teacher user."""
    recipient_email = (getattr(teacher_user, "email", "") or "").strip()
    if not recipient_email or not getattr(teacher_user, "is_email_verified", False):
        logger.info(
            "Teacher %s has no verified email address; skipping email (%s).",
            teacher_user.id,
            dedup_key,
        )
        return False

    if not cache.add(dedup_key, True, timeout=86400):
        logger.info("Duplicate email suppressed for %s.", dedup_key)
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    finance_url = f"{site_url}{reverse('payments:teacher_finance')}"
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tutorbooking.local")

    context = {
        "teacher_name": teacher_user.first_name or teacher_user.username,
        "finance_url": finance_url,
        "header_title": site_name,
        "header_subtitle": header_subtitle,
        "footer_brand": site_name,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
        **extra_context,
    }
    text_content = render_to_string(text_template, context)
    html_content = render_to_string(html_template, context)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=from_email,
        to=[recipient_email],
        headers=headers or {},
    )
    email.attach_alternative(html_content, "text/html")

    try:
        email.send(fail_silently=False)
    except Exception:
        cache.delete(dedup_key)
        raise

    logger.info("Sent teacher alert email (%s) to %s.", dedup_key, recipient_email)
    return True


def _format_dt_for_teacher(dt, teacher_user, fallback: str) -> str:
    if not dt:
        return fallback
    tz_name = getattr(teacher_user, "timezone", "") or "UTC"
    return (
        timezone.localtime(dt, ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M")
        + f" ({tz_name})"
    )


def send_receiving_address_change_alert_email(payment_address) -> bool:
    """
    Send a high-priority security alert email to the teacher's verified email
    informing them of a receiving address change under the 24-hour cooling-off window.
    Uses cache-backed deduplication and converts timestamps into the teacher's timezone.
    """
    teacher_user = payment_address.tenant.teacher_profile.user
    activates_ts = (
        int(payment_address.activates_at.timestamp())
        if payment_address.activates_at
        else 0
    )
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    return _send_dedup_teacher_email(
        teacher_user=teacher_user,
        dedup_key=f"receiving_address_change_alert:{payment_address.id}:{activates_ts}",
        subject=f"[Security Alert] Receiving Address Change Scheduled – {site_name}",
        text_template="payments/emails/receiving_address_change_alert.txt",
        html_template="payments/emails/receiving_address_change_alert.html",
        header_subtitle="Security Alert",
        headers={"X-Priority": "1", "Importance": "high"},
        extra_context={
            "pending_address": payment_address.address,
            "network": "BNB Smart Chain (BEP-20)",
            "activates_at_str": _format_dt_for_teacher(
                payment_address.activates_at, teacher_user, "in 24 hours"
            ),
        },
    )


def send_topup_claim_alert_email(order) -> bool:
    """
    Send an alert email to the tenant's teacher when a student submits an
    "I Have Paid" top-up claim, deduplicated per claim timestamp.
    """
    teacher_user = order.tenant.teacher_profile.user
    claimed_ts = int(order.claimed_at.timestamp()) if order.claimed_at else 0
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    student_name = order.student.get_full_name() or order.student.username
    return _send_dedup_teacher_email(
        teacher_user=teacher_user,
        dedup_key=f"topup_claim_alert:{order.id}:{claimed_ts}",
        subject=(
            f"[Top-Up Claim] ${order.amount:.2f} {order.currency} from {student_name} – "
            f"{site_name}"
        ),
        text_template="payments/emails/topup_claim_alert.txt",
        html_template="payments/emails/topup_claim_alert.html",
        header_subtitle="Top-Up Claim Alert",
        extra_context={
            "order": order,
            "student_name": student_name,
            "txhash": order.claimed_txhash,
            "paid_at_str": _format_dt_for_teacher(
                order.claimed_paid_at or order.claimed_at, teacher_user, "Just now"
            ),
        },
    )
