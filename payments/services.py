import csv
import hashlib
import hmac
import io
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from payments.exceptions import (
    PayoutConflictError,
    PayoutInsufficientBalanceError,
)
from payments.models import Order, Payment, PaymentAddress, Payout
from wallet.exceptions import (
    TenantMismatchError,
    WalletFrozenError,
)
from wallet.models import LedgerEntry, Wallet
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


def build_manual_external_id(tenant_id: int, reference_id: str) -> str:
    """Return the prefixed external_id used for manual top-up Payments."""
    return f"tenant_{tenant_id}:manual_{reference_id.strip()}"


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
    external_id = build_manual_external_id(tenant.id, clean_ref)

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


def create_payout_request(
    *,
    tenant: "Tenant",
    student: "User",
    amount_gross: Decimal,
    destination: str,
    provider: str = "crypto_usdt",
) -> Payout:
    """
    Creates a new Payout request for a student with status PENDING_CONFIRMATION.
    Validates amount > 0 and available_balance >= amount_gross.
    Generates a 32-byte cryptographic token, stores its SHA-256 hash in confirmation_token_hash,
    sets confirmation_expires_at, and dispatches a basic transactional confirmation email.
    Places NO hold on the student's wallet balance at this stage.
    """
    if not isinstance(amount_gross, Decimal) or amount_gross <= Decimal("0.00"):
        raise ValidationError("Payout amount must be a positive Decimal.")

    if not destination or not destination.strip():
        raise ValidationError("Destination address cannot be empty.")

    clean_destination = destination.strip()

    wallet = get_or_create_wallet(
        tenant=tenant,
        user=student,
        currency=tenant.default_currency,
    )

    if wallet.available_balance < amount_gross:
        raise PayoutInsufficientBalanceError(
            f"Insufficient available balance ({wallet.available_balance}) for requested payout of {amount_gross}."
        )

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    confirmation_hours = getattr(settings, "PAYOUT_CONFIRMATION_HOURS", 24)
    now = timezone.now()
    expires_at = now + timedelta(hours=confirmation_hours)

    payout = Payout.objects.create(
        tenant=tenant,
        student=student,
        provider=provider,
        destination=clean_destination,
        amount_gross=amount_gross,
        fee_deducted=Decimal("0.00"),
        amount_net=amount_gross,
        confirmation_token_hash=token_hash,
        confirmation_expires_at=expires_at,
        status=Payout.Status.PENDING_CONFIRMATION,
    )

    # Attach raw token to in-memory instance for caller convenience
    payout.raw_token = raw_token

    # Dispatch basic confirmation email
    subject = "Confirm Your Payout Request"
    message = (
        f"Hello {student.first_name or student.username},\n\n"
        f"You requested a payout of {amount_gross} {tenant.default_currency} to {clean_destination}.\n\n"
        f"Please verify and confirm your payout request using the following confirmation token:\n"
        f"{raw_token}\n\n"
        f"This confirmation token will expire in {confirmation_hours} hours."
    )
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tutorbooking.local")
    send_mail(
        subject=subject,
        message=message,
        from_email=from_email,
        recipient_list=[student.email],
        fail_silently=False,
    )

    logger.info(
        "Payout request created: payout_id=%s tenant_id=%s student_id=%s amount=%s expires_at=%s",
        payout.id,
        tenant.id,
        student.id,
        amount_gross,
        expires_at,
    )

    return payout


def verify_and_reserve_payout(
    *,
    tenant: "Tenant",
    payout: Payout,
    raw_token: str,
) -> LedgerEntry:
    """
    Verifies a student's payout confirmation token and places a PAYOUT_HOLD on their wallet balance.
    Asserts status == PENDING_CONFIRMATION, validates SHA-256 token hash using hmac.compare_digest,
    and checks confirmation_expires_at.
    Acquires row-level locks under transaction.atomic() adhering to the consistent lock hierarchy:
    Wallet -> Payout.
    Re-checks that wallet is not frozen and available_balance >= payout.amount_gross.
    If available balance has dropped, commits CANCELLED status to the DB before raising
    PayoutInsufficientBalanceError.
    On success, transitions payout to REQUESTED, sets confirmation_used_at, and appends a PAYOUT_HOLD
    ledger entry.
    """
    if payout.tenant_id != tenant.id:
        raise TenantMismatchError(
            f"Payout {payout.pk} does not belong to tenant {tenant.pk}."
        )

    if payout.status != Payout.Status.PENDING_CONFIRMATION:
        raise ValidationError(
            f"Payout {payout.pk} is in status '{payout.status}'; expected PENDING_CONFIRMATION."
        )

    if not payout.confirmation_token_hash:
        raise ValidationError("Payout has no confirmation token hash.")

    if not raw_token:
        raise ValidationError("Confirmation token cannot be empty.")

    computed_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(computed_hash, payout.confirmation_token_hash):
        raise ValidationError("Invalid payout confirmation token.")

    now = timezone.now()
    if payout.confirmation_expires_at and now > payout.confirmation_expires_at:
        raise ValidationError("Payout confirmation token has expired.")

    insufficient_balance = False
    entry = None

    with transaction.atomic():
        wallet = get_or_create_wallet(
            tenant=tenant,
            user=payout.student,
            currency=tenant.default_currency,
        )
        # Consistent lock order: Wallet -> Payout
        locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
        locked_payout = Payout.objects.select_for_update().get(pk=payout.pk)

        if locked_payout.status != Payout.Status.PENDING_CONFIRMATION:
            raise ValidationError(
                f"Payout {locked_payout.pk} is in status '{locked_payout.status}'; expected PENDING_CONFIRMATION."
            )

        if locked_wallet.is_frozen:
            raise WalletFrozenError(
                f"Wallet {locked_wallet.pk} is frozen; cannot reserve payout funds."
            )

        if locked_wallet.available_balance < locked_payout.amount_gross:
            # Guardrail 1: Commit CANCELLED to the DB before raising the error
            locked_payout.status = Payout.Status.CANCELLED
            locked_payout.save(update_fields=["status"])
            insufficient_balance = True
        else:
            locked_wallet.reserved_balance += locked_payout.amount_gross
            locked_wallet.save(update_fields=["reserved_balance", "updated_at"])

            now = timezone.now()
            locked_payout.status = Payout.Status.REQUESTED
            locked_payout.confirmation_used_at = now
            locked_payout.save(update_fields=["status", "confirmation_used_at"])

            idempotency_key = f"tenant:{tenant.id}:payout:{locked_payout.id}:hold"
            entry = LedgerEntry.objects.create(
                tenant=tenant,
                wallet=locked_wallet,
                entry_type=LedgerEntry.EntryType.PAYOUT_HOLD,
                amount=locked_payout.amount_gross,
                settled_after=locked_wallet.settled_balance,
                reserved_after=locked_wallet.reserved_balance,
                idempotency_key=idempotency_key,
                reference_id=f"payout_{locked_payout.id}",
                description=f"Payout hold: payout #{locked_payout.id}",
            )

    # Sync memory instance
    payout.status = locked_payout.status
    payout.confirmation_used_at = locked_payout.confirmation_used_at
    wallet.reserved_balance = locked_wallet.reserved_balance

    if insufficient_balance:
        raise PayoutInsufficientBalanceError(
            f"Available balance ({locked_wallet.available_balance}) dropped below requested payout amount "
            f"({locked_payout.amount_gross}). Payout marked CANCELLED."
        )

    logger.info(
        "Payout verified and funds reserved: payout_id=%s tenant_id=%s student_id=%s amount=%s entry_id=%s",
        payout.id,
        tenant.id,
        payout.student_id,
        payout.amount_gross,
        entry.id,
    )

    return entry


def _execute_payout_hold_release(
    *,
    tenant: "Tenant",
    locked_wallet: Wallet,
    locked_payout: Payout,
    target_status: str,
    description: str,
) -> LedgerEntry:
    """
    Internal helper to decrement reserved_balance, update payout status, and append a PAYOUT_RELEASE entry.
    Caller must have acquired row locks on locked_wallet and locked_payout inside transaction.atomic().
    """
    if locked_wallet.reserved_balance < locked_payout.amount_gross:
        raise IntegrityError(
            f"Corrupt state: reserved balance ({locked_wallet.reserved_balance}) is less than payout hold ({locked_payout.amount_gross})."
        )

    locked_wallet.reserved_balance -= locked_payout.amount_gross
    locked_wallet.save(update_fields=["reserved_balance", "updated_at"])

    locked_payout.status = target_status
    locked_payout.save(update_fields=["status"])

    idempotency_key = f"tenant:{tenant.id}:payout:{locked_payout.id}:release"
    return LedgerEntry.objects.create(
        tenant=tenant,
        wallet=locked_wallet,
        entry_type=LedgerEntry.EntryType.PAYOUT_RELEASE,
        amount=locked_payout.amount_gross,
        settled_after=locked_wallet.settled_balance,
        reserved_after=locked_wallet.reserved_balance,
        idempotency_key=idempotency_key,
        reference_id=f"payout_{locked_payout.id}",
        description=description,
    )


def cancel_payout_request(
    *,
    tenant: "Tenant",
    payout: Payout,
    user: "User",
) -> LedgerEntry:
    """
    Cancels an active payout request and releases the held balance back to the student's available funds.
    Strictly permitted ONLY when payout.status == REQUESTED.
    Raises PayoutConflictError (HTTP 409) if status is PROCESSING, COMPLETED, CANCELLED, PENDING_CONFIRMATION, etc.
    Adheres to the consistent lock ordering: Wallet -> Payout under transaction.atomic().
    Emits an immutable PAYOUT_RELEASE ledger entry.
    """
    if payout.tenant_id != tenant.id:
        raise TenantMismatchError(
            f"Payout {payout.pk} does not belong to tenant {tenant.pk}."
        )

    if (
        payout.student_id != user.id
        and not getattr(user, "is_staff", False)
        and not getattr(user, "is_superuser", False)
    ):
        raise PermissionDenied(
            "Only the requesting student or staff can cancel this payout."
        )

    with transaction.atomic():
        wallet = get_or_create_wallet(
            tenant=tenant,
            user=payout.student,
            currency=tenant.default_currency,
        )
        # Consistent lock order: Wallet -> Payout
        locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
        locked_payout = Payout.objects.select_for_update().get(pk=payout.pk)

        if locked_payout.status != Payout.Status.REQUESTED:
            raise PayoutConflictError(
                f"Cannot cancel payout in '{locked_payout.status}' status. "
                "Payout cancellation is strictly allowed only when status is 'REQUESTED'."
            )

        entry = _execute_payout_hold_release(
            tenant=tenant,
            locked_wallet=locked_wallet,
            locked_payout=locked_payout,
            target_status=Payout.Status.CANCELLED,
            description=f"Payout release via cancellation: payout #{locked_payout.id}",
        )

    # Sync memory instance
    payout.status = locked_payout.status
    wallet.reserved_balance = locked_wallet.reserved_balance

    logger.info(
        "Payout cancelled and hold released: payout_id=%s tenant_id=%s student_id=%s amount=%s entry_id=%s",
        payout.id,
        tenant.id,
        payout.student_id,
        payout.amount_gross,
        entry.id,
    )

    return entry


DUPLICATE_PAYOUT_EXTERNAL_REF_ERROR = (
    "A payout with this transaction hash / external reference already exists."
)


def has_duplicate_payout_external_ref(
    *,
    provider: str,
    external_ref: str,
    exclude_payout_id: Optional[int] = None,
) -> bool:
    """Return True if another Payout with the same provider already uses external_ref."""
    qs = Payout.objects.filter(provider=provider, external_ref=external_ref)
    if exclude_payout_id is not None:
        qs = qs.exclude(pk=exclude_payout_id)
    return qs.exists()


def _schedule_payout_status_email(
    *,
    student: "User",
    subject: str,
    body_lines: list[str],
) -> None:
    """Schedule a transactional payout status email on transaction commit."""
    recipient_email = (getattr(student, "email", "") or "").strip()
    if not recipient_email:
        return

    student_name = student.first_name or student.username
    message = f"Hello {student_name},\n\n" + "\n".join(body_lines)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tutorbooking.local")

    def _send():
        send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=[recipient_email],
            fail_silently=False,
        )

    transaction.on_commit(_send)


def mark_payout_processing(
    *,
    tenant: "Tenant",
    payout: Payout,
    admin_user: Optional["User"] = None,
) -> Payout:
    """
    Transitions a payout from REQUESTED to PROCESSING status.
    Acquires row-level locks under transaction.atomic() with consistent hierarchy: Wallet -> Payout.
    Once in PROCESSING, student cancellation is strictly prohibited (raises 409 Conflict).
    """
    if payout.tenant_id != tenant.id:
        raise TenantMismatchError(
            f"Payout {payout.pk} does not belong to tenant {tenant.pk}."
        )

    with transaction.atomic():
        wallet = get_or_create_wallet(
            tenant=tenant,
            user=payout.student,
            currency=tenant.default_currency,
        )
        # Consistent lock order: Wallet -> Payout
        Wallet.objects.select_for_update().get(pk=wallet.pk)
        locked_payout = Payout.objects.select_for_update().get(pk=payout.pk)

        if locked_payout.status != Payout.Status.REQUESTED:
            raise PayoutConflictError(
                f"Cannot mark payout as processing: status is '{locked_payout.status}', expected 'REQUESTED'."
            )

        now = timezone.now()
        locked_payout.status = Payout.Status.PROCESSING
        locked_payout.processed_at = now
        locked_payout.save(update_fields=["status", "processed_at"])

        _schedule_payout_status_email(
            student=locked_payout.student,
            subject=f"Payout Request #{locked_payout.id} Is Being Processed",
            body_lines=[
                (
                    f"Your refund request #{locked_payout.id} for "
                    f"${locked_payout.amount_net:.2f} {tenant.default_currency} "
                    f"to {locked_payout.destination} is now marked as PROCESSING."
                ),
                "",
                (
                    "Your teacher is executing the transfer and you will receive another "
                    "confirmation once the transaction reference is recorded."
                ),
            ],
        )

    # Sync memory instance
    payout.status = locked_payout.status
    payout.processed_at = locked_payout.processed_at

    logger.info(
        "Payout marked processing: payout_id=%s tenant_id=%s admin_user_id=%s",
        payout.id,
        tenant.id,
        getattr(admin_user, "id", None),
    )

    return locked_payout


def settle_payout(
    *,
    tenant: "Tenant",
    payout: Payout,
    external_ref: str,
    admin_user: Optional["User"] = None,
) -> LedgerEntry:
    """
    Finalizes a payout by transitioning status from PROCESSING to COMPLETED, recording the external_ref,
    and decrementing both settled_balance and reserved_balance on the student's wallet via a PAYOUT_SETTLE entry.
    Acquires row-level locks under transaction.atomic() adhering to Wallet -> Payout lock hierarchy.
    """
    if payout.tenant_id != tenant.id:
        raise TenantMismatchError(
            f"Payout {payout.pk} does not belong to tenant {tenant.pk}."
        )

    if not external_ref or not external_ref.strip():
        raise ValidationError("External reference cannot be empty.")

    clean_ref = external_ref.strip()

    with transaction.atomic():
        wallet = get_or_create_wallet(
            tenant=tenant,
            user=payout.student,
            currency=tenant.default_currency,
        )
        # Consistent lock order: Wallet -> Payout
        locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
        locked_payout = Payout.objects.select_for_update().get(pk=payout.pk)

        if locked_payout.status != Payout.Status.PROCESSING:
            raise PayoutConflictError(
                f"Cannot settle payout: status is '{locked_payout.status}', expected 'PROCESSING'."
            )

        if has_duplicate_payout_external_ref(
            provider=locked_payout.provider,
            external_ref=clean_ref,
            exclude_payout_id=locked_payout.pk,
        ):
            raise ValidationError(DUPLICATE_PAYOUT_EXTERNAL_REF_ERROR)

        if locked_wallet.settled_balance < locked_payout.amount_gross:
            raise PayoutInsufficientBalanceError(
                f"Settled balance ({locked_wallet.settled_balance}) is less than payout amount ({locked_payout.amount_gross})."
            )
        if locked_wallet.reserved_balance < locked_payout.amount_gross:
            raise IntegrityError(
                f"Reserved balance ({locked_wallet.reserved_balance}) is less than payout hold ({locked_payout.amount_gross})."
            )

        locked_wallet.settled_balance -= locked_payout.amount_gross
        locked_wallet.reserved_balance -= locked_payout.amount_gross
        locked_wallet.save(
            update_fields=["settled_balance", "reserved_balance", "updated_at"]
        )

        locked_payout.status = Payout.Status.COMPLETED
        locked_payout.external_ref = clean_ref
        locked_payout.processed_at = timezone.now()
        locked_payout.save(update_fields=["status", "external_ref", "processed_at"])

        idempotency_key = f"tenant:{tenant.id}:payout:{locked_payout.id}:settle"
        entry = LedgerEntry.objects.create(
            tenant=tenant,
            wallet=locked_wallet,
            entry_type=LedgerEntry.EntryType.PAYOUT_SETTLE,
            amount=locked_payout.amount_gross,
            settled_after=locked_wallet.settled_balance,
            reserved_after=locked_wallet.reserved_balance,
            idempotency_key=idempotency_key,
            reference_id=clean_ref,
            description=f"Payout settled: {clean_ref} for payout #{locked_payout.id}"[
                :255
            ],
        )

        _schedule_payout_status_email(
            student=locked_payout.student,
            subject=f"Payout Request #{locked_payout.id} Completed",
            body_lines=[
                (
                    f"Your refund request #{locked_payout.id} for "
                    f"${locked_payout.amount_net:.2f} {tenant.default_currency} "
                    f"to {locked_payout.destination} has been completed."
                ),
                "",
                f"Transaction reference: {clean_ref}",
                "This settlement has been recorded in your wallet ledger.",
            ],
        )

    # Sync memory instance
    payout.status = locked_payout.status
    payout.external_ref = locked_payout.external_ref
    payout.processed_at = locked_payout.processed_at
    wallet.settled_balance = locked_wallet.settled_balance
    wallet.reserved_balance = locked_wallet.reserved_balance

    logger.info(
        "Payout settled: payout_id=%s tenant_id=%s amount=%s external_ref=%s entry_id=%s",
        payout.id,
        tenant.id,
        payout.amount_gross,
        clean_ref,
        entry.id,
    )

    return entry


def release_payout(
    *,
    tenant: "Tenant",
    payout: Payout,
    reason: str = "",
    admin_user: Optional["User"] = None,
) -> LedgerEntry:
    """
    Rejects a payout from REQUESTED or PROCESSING status, transitioning it to REJECTED and releasing
    the held balance back to the student's available funds via a PAYOUT_RELEASE ledger entry.
    Acquires row-level locks under transaction.atomic() adhering to Wallet -> Payout lock hierarchy.
    """
    if payout.tenant_id != tenant.id:
        raise TenantMismatchError(
            f"Payout {payout.pk} does not belong to tenant {tenant.pk}."
        )

    clean_reason = reason.strip()

    with transaction.atomic():
        wallet = get_or_create_wallet(
            tenant=tenant,
            user=payout.student,
            currency=tenant.default_currency,
        )
        # Consistent lock order: Wallet -> Payout
        locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
        locked_payout = Payout.objects.select_for_update().get(pk=payout.pk)

        if locked_payout.status not in (
            Payout.Status.REQUESTED,
            Payout.Status.PROCESSING,
        ):
            raise PayoutConflictError(
                f"Cannot release payout in '{locked_payout.status}' status. "
                "Only payouts in 'REQUESTED' or 'PROCESSING' status can be rejected/released."
            )

        entry_description = (
            f"Payout rejected ({clean_reason}): payout #{locked_payout.id}"[:255]
            if clean_reason
            else f"Payout rejected: payout #{locked_payout.id}"
        )
        entry = _execute_payout_hold_release(
            tenant=tenant,
            locked_wallet=locked_wallet,
            locked_payout=locked_payout,
            target_status=Payout.Status.REJECTED,
            description=entry_description,
        )

        locked_payout.processed_at = timezone.now()
        locked_payout.save(update_fields=["processed_at"])

        body_lines = [
            (
                f"Your refund request #{locked_payout.id} for "
                f"${locked_payout.amount_gross:.2f} {tenant.default_currency} "
                f"has been rejected."
            ),
            "",
        ]
        if clean_reason:
            body_lines.extend([f"Reason: {clean_reason}", ""])
        body_lines.append(
            f"The held amount (${locked_payout.amount_gross:.2f} {tenant.default_currency}) "
            "has been released back to your available wallet balance."
        )
        _schedule_payout_status_email(
            student=locked_payout.student,
            subject=f"Payout Request #{locked_payout.id} Rejected",
            body_lines=body_lines,
        )

    # Sync memory instance
    payout.status = locked_payout.status
    payout.processed_at = locked_payout.processed_at
    wallet.reserved_balance = locked_wallet.reserved_balance

    logger.info(
        "Payout released/rejected: payout_id=%s tenant_id=%s student_id=%s amount=%s reason=%s entry_id=%s",
        payout.id,
        tenant.id,
        payout.student_id,
        payout.amount_gross,
        clean_reason,
        entry.id,
    )

    return entry


FORMULA_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_MANUAL_PREFIX_RE = re.compile(r"^tenant_\d+:manual_")
_MANUAL_TOPUP_DESC_RE = re.compile(r"^Manual top-up:\s*tenant_\d+:manual_")


def get_user_display_name(user) -> str:
    """Return full name if present, otherwise username."""
    return user.get_full_name() or user.username


def to_viewer_local(dt: Optional[datetime], viewer_tz: ZoneInfo) -> Optional[datetime]:
    """Convert an aware datetime to naive local time in viewer_tz for template/CSV display."""
    if dt is None:
        return None
    return timezone.localtime(dt, viewer_tz).replace(tzinfo=None)


def strip_manual_prefix(reference_id: str) -> str:
    return _MANUAL_PREFIX_RE.sub("", reference_id or "")


def attach_ledger_display_fields(
    entries: list[LedgerEntry], tenant: "Tenant", viewer_tz: ZoneInfo
) -> None:
    """Attach localized timestamps, student names, stripped references, and manual notes."""
    manual_refs = [
        e.reference_id for e in entries if _MANUAL_PREFIX_RE.match(e.reference_id or "")
    ]
    notes_by_external_id: dict[str, str] = {}
    if manual_refs:
        for payment in Payment.objects.filter(
            tenant=tenant,
            external_id__in=manual_refs,
        ).only("external_id", "raw_metadata"):
            note = ((payment.raw_metadata or {}).get("notes") or "").strip()
            if note:
                notes_by_external_id[payment.external_id] = note

    for entry in entries:
        entry.local_created_at = to_viewer_local(entry.created_at, viewer_tz)
        entry.student_display_name = get_user_display_name(entry.wallet.user)
        entry.display_reference = strip_manual_prefix(entry.reference_id)

        custom_note = notes_by_external_id.get(entry.reference_id)
        if custom_note:
            entry.display_description = custom_note
        elif _MANUAL_TOPUP_DESC_RE.match(entry.description or ""):
            entry.display_description = "Manual top-up"
        else:
            entry.display_description = entry.description


def sanitize_csv_cell(value: object) -> str:
    """
    Sanitize a CSV cell value against spreadsheet formula injection by prepending a
    single quote to any value beginning with '=', '+', '-', '@', '\\t', or '\\r'.
    """
    if value is None:
        return ""
    text = str(value)
    if text.startswith(FORMULA_INJECTION_PREFIXES):
        return f"'{text}"
    return text


def build_tenant_ledger_csv(tenant: "Tenant", viewer_tz: ZoneInfo) -> str:
    """
    Export all immutable ledger entries for a tenant as a formula-sanitized CSV string.
    """
    entries = list(
        LedgerEntry.objects.filter(tenant=tenant)
        .select_related("wallet", "wallet__user")
        .order_by("-created_at", "-id")
    )
    attach_ledger_display_fields(entries, tenant, viewer_tz)

    headers = [
        "Entry ID",
        "Timestamp",
        "Student",
        "Student Email",
        "Entry Type",
        "Currency",
        "Amount",
        "Settled Balance After",
        "Reserved Balance After",
        "Reference",
        "Description",
        "Idempotency Key",
    ]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([sanitize_csv_cell(h) for h in headers])

    for entry in entries:
        timestamp_str = (
            entry.local_created_at.strftime("%Y-%m-%d %H:%M:%S")
            if entry.local_created_at
            else ""
        )
        row = [
            entry.id,
            timestamp_str,
            entry.student_display_name,
            entry.wallet.user.email or "",
            entry.entry_type,
            entry.wallet.currency,
            f"{entry.amount:.2f}",
            f"{entry.settled_after:.2f}",
            f"{entry.reserved_after:.2f}",
            entry.display_reference,
            entry.display_description,
            entry.idempotency_key,
        ]
        writer.writerow([sanitize_csv_cell(cell) for cell in row])

    return buffer.getvalue()
