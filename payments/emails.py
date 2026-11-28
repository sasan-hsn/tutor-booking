import logging
from typing import NamedTuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


def _render_and_send_multipart_email(
    *,
    recipient_email: str,
    subject: str,
    text_template: str,
    html_template: str,
    extra_context: dict,
    header_subtitle: str,
    headers: dict | None = None,
) -> None:
    """Build shared site branding context, render multipart templates, and send email."""
    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tutorbooking.local")

    context = {
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
    email.send(fail_silently=False)


def build_dedup_key(entity_id: str, action: str) -> str:
    """Return a standardized cache deduplication key for transactional notifications."""
    return f"dedup:event:{entity_id}:{action}"


def send_dedup_multipart_email(
    *,
    recipient_email: str,
    dedup_key: str,
    subject: str,
    text_template: str,
    html_template: str,
    extra_context: dict,
    header_subtitle: str,
    headers: dict | None = None,
    timeout: int = 86400,
) -> bool:
    """
    Atomically claim a dedup key via cache.add and send a multipart branded email.
    Deletes the dedup key on exception so retries are never permanently blocked.
    """
    clean_email = (recipient_email or "").strip()
    if not clean_email:
        logger.info("No recipient email provided; skipping email (%s).", dedup_key)
        return False

    if not cache.add(dedup_key, True, timeout=timeout):
        logger.info("Duplicate email suppressed for %s.", dedup_key)
        return False

    try:
        _render_and_send_multipart_email(
            recipient_email=clean_email,
            subject=subject,
            text_template=text_template,
            html_template=html_template,
            header_subtitle=header_subtitle,
            headers=headers,
            extra_context=extra_context,
        )
    except Exception:
        cache.delete(dedup_key)
        raise

    logger.info("Sent email (%s) to %s.", dedup_key, clean_email)
    return True


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
    require_verified: bool = True,
) -> bool:
    """Send a deduplicated multipart email to a teacher user."""
    recipient_email = (getattr(teacher_user, "email", "") or "").strip()
    if not recipient_email or (
        require_verified and not getattr(teacher_user, "is_email_verified", False)
    ):
        logger.info(
            "Teacher %s has no eligible email address; skipping email (%s).",
            teacher_user.id,
            dedup_key,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    finance_url = f"{site_url}{reverse('payments:teacher_finance')}"

    return send_dedup_multipart_email(
        recipient_email=recipient_email,
        dedup_key=dedup_key,
        subject=subject,
        text_template=text_template,
        html_template=html_template,
        header_subtitle=header_subtitle,
        headers=headers,
        extra_context={
            "teacher_name": teacher_user.first_name or teacher_user.username,
            "finance_url": finance_url,
            **extra_context,
        },
    )


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
        dedup_key=build_dedup_key(
            f"address_{payment_address.id}", f"change_{activates_ts}"
        ),
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
    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    claim_url = f"{site_url}{reverse('payments:teacher_finance')}?claim_id={order.id}"
    student_name = order.student.get_full_name() or order.student.username
    return _send_dedup_teacher_email(
        teacher_user=teacher_user,
        dedup_key=build_dedup_key(f"order_{order.id}", f"topup_claim_{claimed_ts}"),
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
            "claim_url": claim_url,
            "paid_at_str": _format_dt_for_teacher(
                order.claimed_paid_at or order.claimed_at, teacher_user, "Just now"
            ),
        },
    )


def send_topup_credited_email(payment) -> bool:
    """
    Send a branded deposit receipt email to the student confirming a credited
    manual or on-chain top-up, updated available balance, and ledger reference.
    """
    from payments.services import strip_manual_prefix
    from wallet.models import Wallet

    student = (
        payment.order.student
        if getattr(payment, "order", None) is not None
        else payment.claimed_by
    )
    if student is None:
        return False

    recipient_email = (getattr(student, "email", "") or "").strip()
    if not recipient_email:
        return False

    wallet = Wallet.objects.filter(
        tenant=payment.tenant,
        user=student,
        currency=payment.currency,
    ).first()
    available_balance = (
        wallet.available_balance if wallet is not None else payment.net_amount
    )

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    wallet_url = f"{site_url}{reverse('wallet:student_wallet')}"
    reference_display = strip_manual_prefix(payment.external_id) or payment.external_id
    notes = ((payment.raw_metadata or {}).get("notes") or "").strip()

    return send_dedup_multipart_email(
        recipient_email=recipient_email,
        dedup_key=build_dedup_key(f"payment_{payment.id}", "topup_credited"),
        subject=(
            f"Wallet Top-Up Credited (+${payment.net_amount:.2f} {payment.currency}) – "
            f"{site_name}"
        ),
        text_template="payments/emails/topup_credited.txt",
        html_template="payments/emails/topup_credited.html",
        header_subtitle="Top-Up Credited",
        extra_context={
            "payment": payment,
            "student_name": student.first_name or student.username,
            "amount_credited": payment.net_amount,
            "currency": payment.currency,
            "available_balance": available_balance,
            "reference_display": reference_display,
            "order_id": payment.order_id,
            "notes": notes,
            "wallet_url": wallet_url,
        },
    )


def send_payout_confirmation_email(
    payout,
    raw_token: str,
    confirmation_hours: int = 24,
) -> bool:
    """
    Send the Stage 1 payout confirmation link email to the requesting student.
    Uses an absolute URL built from settings.SITE_URL and atomic cache deduplication.
    """
    student = payout.student
    recipient_email = (getattr(student, "email", "") or "").strip()
    if not recipient_email:
        logger.info(
            "Student %s has no email address; skipping payout confirmation email for Payout #%s.",
            student.id,
            payout.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    confirm_path = reverse(
        "wallet:student_payout_confirm",
        kwargs={"raw_token": raw_token},
    )
    confirmation_url = f"{site_url}{confirm_path}"

    return send_dedup_multipart_email(
        recipient_email=recipient_email,
        dedup_key=build_dedup_key(f"payout_{payout.id}", "confirmation"),
        subject=f"Confirm Your Refund Request – {site_name}",
        text_template="payments/emails/payout_confirmation.txt",
        html_template="payments/emails/payout_confirmation.html",
        header_subtitle="Refund Request Confirmation",
        extra_context={
            "payout": payout,
            "student_name": student.first_name or student.username,
            "amount_gross": payout.amount_gross,
            "currency": payout.tenant.default_currency,
            "destination": payout.destination,
            "notes": payout.notes,
            "confirmation_url": confirmation_url,
            "confirmation_hours": confirmation_hours,
        },
    )


class _PayoutStatusSpec(NamedTuple):
    status_label: str
    subject_verb: str
    heading: str
    intro_clause: str
    detail_text: str


_PAYOUT_STATUS_SPECS: dict[str, _PayoutStatusSpec] = {
    "COMPLETED": _PayoutStatusSpec(
        status_label="Completed",
        subject_verb="Completed",
        heading="Your Refund Has Been Completed",
        intro_clause="has been completed.",
        detail_text=(
            "Your payout transfer has been recorded with the transaction reference above "
            "and settled in your wallet ledger."
        ),
    ),
    "REJECTED": _PayoutStatusSpec(
        status_label="Rejected",
        subject_verb="Rejected",
        heading="Your Refund Request Was Rejected",
        intro_clause="could not be processed and was rejected.",
        detail_text="",
    ),
    "PROCESSING": _PayoutStatusSpec(
        status_label="Processing",
        subject_verb="Is Being Processed",
        heading="Your Refund Request Is Being Processed",
        intro_clause="is now marked as PROCESSING.",
        detail_text=(
            "Your teacher is executing the transfer and you will receive another "
            "confirmation once the transaction reference is recorded."
        ),
    ),
}


def send_payout_status_email(
    payout,
    *,
    status: str,
    reason: str = "",
) -> bool:
    """
    Send a branded HTML+TXT payout status notification (PROCESSING, COMPLETED, REJECTED)
    to the requesting student using atomic deduplication.
    """
    from payments.models import Payout

    student = payout.student
    recipient_email = (getattr(student, "email", "") or "").strip()
    if not recipient_email:
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    wallet_url = f"{site_url}{reverse('wallet:student_wallet')}"
    currency = payout.tenant.default_currency

    spec = _PAYOUT_STATUS_SPECS.get(status, _PAYOUT_STATUS_SPECS["PROCESSING"])
    amount_value = (
        payout.amount_gross if status == Payout.Status.REJECTED else payout.amount_net
    )
    subject_title = f"Refund Request #{payout.id} {spec.subject_verb}"
    detail_text = spec.detail_text or (
        f"The held amount (${payout.amount_gross:.2f} {currency}) has been released "
        "back to your available wallet balance."
    )

    return send_dedup_multipart_email(
        recipient_email=recipient_email,
        dedup_key=build_dedup_key(f"payout_{payout.id}", f"status_{status.lower()}"),
        subject=f"{subject_title} – {site_name}",
        text_template="payments/emails/payout_status_update.txt",
        html_template="payments/emails/payout_status_update.html",
        header_subtitle=f"Refund {spec.status_label}",
        extra_context={
            "payout": payout,
            "student_name": student.first_name or student.username,
            "status_code": status,
            "status_label": spec.status_label,
            "subject_title": subject_title,
            "heading": spec.heading,
            "intro_text": f"your refund request #{payout.id} {spec.intro_clause}",
            "detail_text": detail_text,
            "amount_display": f"{amount_value:.2f}",
            "currency": currency,
            "destination": payout.destination,
            "external_ref": payout.external_ref or "",
            "reason": (reason or "").strip(),
            "wallet_url": wallet_url,
        },
    )


def send_payout_reminder_48h_email(payout, *, reminder_hours: int = 48) -> bool:
    """
    Send a branded high-priority 48-hour payout reminder email to the tenant's teacher
    with atomic cache-backed deduplication.
    """
    teacher_user = payout.tenant.teacher_profile.user
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    student_name = payout.student.get_full_name() or payout.student.username
    return _send_dedup_teacher_email(
        teacher_user=teacher_user,
        dedup_key=build_dedup_key(f"payout_{payout.id}", "reminder_48h"),
        subject=f"[Action Required] Refund Request Reminder: Payout #{payout.id} – {site_name}",
        text_template="payments/emails/payout_reminder_48h.txt",
        html_template="payments/emails/payout_reminder_48h.html",
        header_subtitle="Pending Refund Reminder",
        headers={"X-Priority": "1", "Importance": "high"},
        require_verified=False,
        extra_context={
            "payout": payout,
            "student_name": student_name,
            "amount_gross": payout.amount_gross,
            "currency": payout.tenant.default_currency,
            "destination": payout.destination,
            "provider": payout.provider,
            "reminder_hours": reminder_hours,
        },
    )
