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


def send_receiving_address_change_alert_email(payment_address) -> bool:
    """
    Send a high-priority security alert email to the teacher's verified email
    informing them of a receiving address change under the 24-hour cooling-off window.
    Uses cache-backed deduplication and converts timestamps into the teacher's timezone.
    """
    teacher_user = payment_address.tenant.teacher_profile.user
    recipient_email = (getattr(teacher_user, "email", "") or "").strip()
    if not recipient_email or not getattr(teacher_user, "is_email_verified", False):
        logger.info(
            "Teacher %s has no verified email address; skipping receiving address security alert.",
            teacher_user.id,
        )
        return False

    activates_ts = (
        int(payment_address.activates_at.timestamp())
        if payment_address.activates_at
        else 0
    )
    dedup_key = f"receiving_address_change_alert:{payment_address.id}:{activates_ts}"
    if not cache.add(dedup_key, True, timeout=86400):
        logger.info(
            "Duplicate receiving address security alert suppressed for PaymentAddress #%s.",
            payment_address.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    finance_url = f"{site_url}{reverse('payments:teacher_finance')}"
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tutorbooking.local")

    teacher_tz_name = getattr(teacher_user, "timezone", "") or "UTC"
    teacher_tz = ZoneInfo(teacher_tz_name)
    activates_at_str = (
        timezone.localtime(payment_address.activates_at, teacher_tz).strftime(
            "%Y-%m-%d %H:%M"
        )
        + f" ({teacher_tz_name})"
        if payment_address.activates_at
        else "in 24 hours"
    )

    teacher_name = teacher_user.first_name or teacher_user.username
    context = {
        "teacher_name": teacher_name,
        "pending_address": payment_address.address,
        "network": "BNB Smart Chain (BEP-20)",
        "activates_at_str": activates_at_str,
        "finance_url": finance_url,
        "header_title": site_name,
        "header_subtitle": "Security Alert",
        "footer_brand": site_name,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
    }

    subject = f"[Security Alert] Receiving Address Change Scheduled – {site_name}"
    text_content = render_to_string(
        "payments/emails/receiving_address_change_alert.txt", context
    )
    html_content = render_to_string(
        "payments/emails/receiving_address_change_alert.html", context
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=from_email,
        to=[recipient_email],
        headers={"X-Priority": "1", "Importance": "high"},
    )
    email.attach_alternative(html_content, "text/html")

    try:
        email.send(fail_silently=False)
    except Exception:
        cache.delete(dedup_key)
        raise

    logger.info(
        "Sent receiving address security alert to %s for PaymentAddress #%s.",
        recipient_email,
        payment_address.id,
    )
    return True
