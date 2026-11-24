import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from payments.models import Order, Payment, PaymentAddress
from wallet.models import Wallet
from wallet.services import credit_wallet, get_or_create_wallet

if TYPE_CHECKING:
    from accounts.models import Tenant, User

logger = logging.getLogger(__name__)


def _validate_topup_amount(
    tenant: "Tenant",
    amount: Decimal,
    currency: str,
) -> None:
    if not isinstance(amount, Decimal) or amount <= Decimal("0.00"):
        raise ValidationError("Amount must be a positive Decimal.")
    if amount < tenant.min_topup_amount:
        raise ValidationError(
            f"Amount must be at least {tenant.min_topup_amount} {currency}."
        )


def create_topup_order(
    *,
    tenant: "Tenant",
    student: "User",
    amount: Decimal,
    currency: str = "USD",
    idempotency_key: Optional[str] = None,
) -> Order:
    """
    Creates or returns an active unexpired pending top-up Order for the given student and tenant.
    Validates minimum top-up amount and snapshots active payment instructions.
    """
    _validate_topup_amount(tenant, amount, currency)

    now = timezone.now()
    existing_order = (
        Order.objects.filter(
            tenant=tenant,
            student=student,
            status=Order.Status.PENDING,
            expires_at__gt=now,
        )
        .order_by("-created_at")
        .first()
    )
    if existing_order is not None:
        return existing_order

    # Prioritize crypto_usdt active address, fallback to any active address for the tenant
    active_address = (
        PaymentAddress.objects.filter(
            tenant=tenant,
            provider="crypto_usdt",
            status=PaymentAddress.Status.ACTIVE,
        )
        .order_by("id")
        .first()
    )
    if active_address is None:
        active_address = (
            PaymentAddress.objects.filter(
                tenant=tenant,
                status=PaymentAddress.Status.ACTIVE,
            )
            .order_by("id")
            .first()
        )

    if active_address is None:
        raise ValidationError("No active payment address configured for this tenant.")

    snapshot = {
        "provider": active_address.provider,
        "address": active_address.address,
        "receiving_address": active_address.address,
    }

    expiry_hours = getattr(settings, "ORDER_EXPIRY_HOURS", 24)
    expires_at = now + timedelta(hours=expiry_hours)

    if not idempotency_key:
        idempotency_key = f"tenant_{tenant.id}:order_{uuid.uuid4().hex}"

    order = Order.objects.create(
        tenant=tenant,
        student=student,
        amount=amount,
        currency=currency,
        status=Order.Status.PENDING,
        payment_instructions_snapshot=snapshot,
        idempotency_key=idempotency_key,
        expires_at=expires_at,
    )
    return order


def mark_payment_successful(
    *,
    payment: Payment,
    raw_metadata: Optional[dict] = None,
) -> Payment:
    """
    Marks a payment as COMPLETED and credits the student's wallet atomically.
    Executes under transaction.atomic() with consistent lock ordering:
    Order (if linked) -> Payment -> Wallet.
    Idempotent: does nothing if payment is already COMPLETED.
    """
    with transaction.atomic():
        locked_order = None
        if payment.order_id:
            locked_order = Order.objects.select_for_update().get(pk=payment.order_id)
            if locked_order.tenant_id != payment.tenant_id:
                raise ValidationError("Order and Payment tenant mismatch.")

        locked_payment = Payment.objects.select_for_update().get(pk=payment.pk)

        if locked_payment.status == Payment.Status.COMPLETED:
            return locked_payment

        student = locked_order.student if locked_order else locked_payment.claimed_by
        if student is None:
            raise ValidationError("Payment has no associated student.")

        wallet = get_or_create_wallet(
            tenant=locked_payment.tenant,
            user=student,
            currency=locked_payment.currency,
        )
        locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)

        idempotency_key = (
            f"tenant:{locked_payment.tenant_id}:payment:{locked_payment.id}:credit"
        )
        credit_wallet(
            tenant=locked_payment.tenant,
            wallet=locked_wallet,
            amount=locked_payment.net_amount,
            idempotency_key=idempotency_key,
            reference_id=locked_payment.external_id,
            description=f"Manual top-up: {locked_payment.external_id}",
        )

        now = timezone.now()
        locked_payment.status = Payment.Status.COMPLETED
        locked_payment.completed_at = now
        if raw_metadata:
            locked_payment.raw_metadata.update(raw_metadata)
        locked_payment.save(update_fields=["status", "completed_at", "raw_metadata"])

        if locked_order and locked_order.status != Order.Status.COMPLETED:
            locked_order.status = Order.Status.COMPLETED
            locked_order.completed_at = now
            locked_order.save(update_fields=["status", "completed_at"])

        payment.status = locked_payment.status
        payment.completed_at = locked_payment.completed_at
        payment.raw_metadata = locked_payment.raw_metadata
        if payment.order and locked_order:
            payment.order.status = locked_order.status
            payment.order.completed_at = locked_order.completed_at

        return locked_payment


def process_manual_topup(
    *,
    tenant: "Tenant",
    student: "User",
    amount: Decimal,
    reference_id: str,
    admin_user: "User",
    notes: str = "",
    idempotency_key: Optional[str] = None,
) -> Payment:
    """
    Ingests and fulfills an offline manual deposit from a student verified by an admin/teacher.
    Enforces tenant minimum top-up, prefixes external_id with tenant_{id}:manual_{reference_id},
    creates the Payment initially in INITIATED status, and marks it successful via mark_payment_successful.
    Records structured audit log.
    """
    _validate_topup_amount(tenant, amount, tenant.default_currency)

    if not reference_id or not reference_id.strip():
        raise ValidationError("Reference ID cannot be empty.")

    clean_ref = reference_id.strip()
    external_id = f"tenant_{tenant.id}:manual_{clean_ref}"

    with transaction.atomic():
        existing_payment = Payment.objects.filter(external_id=external_id).first()
        if existing_payment is not None:
            if existing_payment.status == Payment.Status.COMPLETED:
                return existing_payment
            return mark_payment_successful(payment=existing_payment)

        # Look for matching active, unexpired pending Order with the same amount
        matching_order = (
            Order.objects.filter(
                tenant=tenant,
                student=student,
                amount=amount,
                status=Order.Status.PENDING,
                expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )

        raw_metadata = {
            "reference_id": clean_ref,
            "admin_user_id": admin_user.id,
            "admin_username": admin_user.username,
            "notes": notes,
        }
        if idempotency_key:
            raw_metadata["idempotency_key"] = idempotency_key

        payment = Payment.objects.create(
            tenant=tenant,
            order=matching_order,
            claimed_by=student,
            provider="manual",
            external_id=external_id,
            gross_amount=amount,
            fee_amount=Decimal("0.00"),
            net_amount=amount,
            currency=tenant.default_currency,
            status=Payment.Status.INITIATED,
            raw_metadata=raw_metadata,
        )

        completed_payment = mark_payment_successful(
            payment=payment,
            raw_metadata=raw_metadata,
        )

        logger.info(
            "Manual top-up completed: payment_id=%s, tenant_id=%s, student_id=%s, amount=%s, admin_user_id=%s, external_id=%s",
            completed_payment.id,
            tenant.id,
            student.id,
            amount,
            admin_user.id,
            external_id,
            extra={
                "payment_id": completed_payment.id,
                "tenant_id": tenant.id,
                "student_id": student.id,
                "amount": str(amount),
                "admin_user_id": admin_user.id,
                "external_id": external_id,
            },
        )

        return completed_payment
