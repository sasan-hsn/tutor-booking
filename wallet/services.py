import logging
import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, DecimalField, F, Sum, Value, When
from django.utils import timezone

from notifications.services import schedule_wallet_balance_updated
from wallet.exceptions import (
    CurrencyMismatchError,
    InsufficientBalanceError,
    InvalidAmountError,
    InvalidEntryTypeError,
    TenantMismatchError,
    WalletFrozenError,
)
from wallet.models import LedgerEntry, Wallet

if TYPE_CHECKING:
    from accounts.models import Tenant, User
    from booking.models import Booking

logger = logging.getLogger(__name__)

ALLOWED_CREDIT_ENTRY_TYPES = frozenset(
    {
        LedgerEntry.EntryType.CREDIT,
        LedgerEntry.EntryType.WAIVER,
        LedgerEntry.EntryType.ADJUSTMENT_CREDIT,
    }
)

SETTLED_INCREMENT_ENTRY_TYPES = frozenset(
    {
        LedgerEntry.EntryType.CREDIT,
        LedgerEntry.EntryType.WAIVER,
        LedgerEntry.EntryType.ADJUSTMENT_CREDIT,
    }
)

SETTLED_DECREMENT_ENTRY_TYPES = frozenset(
    {
        LedgerEntry.EntryType.CONSUME,
        LedgerEntry.EntryType.PAYOUT_SETTLE,
        LedgerEntry.EntryType.ADJUSTMENT_DEBIT,
    }
)

RESERVED_INCREMENT_ENTRY_TYPES = frozenset(
    {
        LedgerEntry.EntryType.RESERVE,
        LedgerEntry.EntryType.PAYOUT_HOLD,
    }
)

RESERVED_DECREMENT_ENTRY_TYPES = frozenset(
    {
        LedgerEntry.EntryType.RELEASE,
        LedgerEntry.EntryType.CONSUME,
        LedgerEntry.EntryType.PAYOUT_SETTLE,
        LedgerEntry.EntryType.PAYOUT_RELEASE,
    }
)

STUDENT_LEDGER_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("CREDIT", "Credit"),
    ("HOLD", "Hold"),
    ("RELEASE", "Release"),
    ("SETTLEMENT", "Settlement"),
    ("REFUND", "Refund"),
)


class _EntryClassification(NamedTuple):
    category: str
    impact_direction: str
    impact_note: str


_STUDENT_ENTRY_CLASSIFICATION: dict[str, _EntryClassification] = {
    LedgerEntry.EntryType.CREDIT: _EntryClassification(
        "CREDIT", "positive", "Added to balance"
    ),
    LedgerEntry.EntryType.WAIVER: _EntryClassification(
        "CREDIT", "positive", "Penalty waiver credit"
    ),
    LedgerEntry.EntryType.ADJUSTMENT_CREDIT: _EntryClassification(
        "CREDIT", "positive", "Balance adjustment"
    ),
    LedgerEntry.EntryType.RESERVE: _EntryClassification(
        "HOLD", "negative", "Reserved for lesson"
    ),
    LedgerEntry.EntryType.RELEASE: _EntryClassification(
        "RELEASE", "positive", "Released from hold"
    ),
    LedgerEntry.EntryType.CONSUME: _EntryClassification(
        "SETTLEMENT", "neutral", "Settled from hold"
    ),
    LedgerEntry.EntryType.ADJUSTMENT_DEBIT: _EntryClassification(
        "SETTLEMENT", "negative", "Balance adjustment"
    ),
    LedgerEntry.EntryType.PAYOUT_HOLD: _EntryClassification(
        "REFUND", "negative", "Reserved for refund"
    ),
    LedgerEntry.EntryType.PAYOUT_RELEASE: _EntryClassification(
        "REFUND", "positive", "Refund hold released"
    ),
    LedgerEntry.EntryType.PAYOUT_SETTLE: _EntryClassification(
        "REFUND", "neutral", "Refund settled from hold"
    ),
}

_STUDENT_CATEGORY_ENTRY_TYPES: dict[str, tuple[str, ...]] = {
    cat_code: tuple(
        entry_type
        for entry_type, spec in _STUDENT_ENTRY_CLASSIFICATION.items()
        if spec.category == cat_code
    )
    for cat_code, _ in STUDENT_LEDGER_CATEGORIES
}

_CATEGORY_LABELS = dict(STUDENT_LEDGER_CATEGORIES)


class StudentLedgerEntryDisplay(NamedTuple):
    category: str
    category_label: str
    impact_direction: str
    formatted_impact: str
    impact_note: str


def classify_student_ledger_entry(
    *,
    entry_type: str,
    amount: Decimal,
) -> StudentLedgerEntryDisplay:
    """
    Map a LedgerEntry.EntryType and amount to a student-facing category and signed
    available-balance net impact without double-counting holds and settlements.
    """
    spec = _STUDENT_ENTRY_CLASSIFICATION.get(
        entry_type,
        _EntryClassification("SETTLEMENT", "neutral", ""),
    )
    abs_amount = abs(amount).quantize(Decimal("0.01"))
    if spec.impact_direction == "positive":
        formatted = f"+${abs_amount:.2f}"
    elif spec.impact_direction == "negative":
        formatted = f"-${abs_amount:.2f}"
    else:
        formatted = f"${abs_amount:.2f}"

    return StudentLedgerEntryDisplay(
        category=spec.category,
        category_label=_CATEGORY_LABELS.get(spec.category, spec.category.title()),
        impact_direction=spec.impact_direction,
        formatted_impact=formatted,
        impact_note=spec.impact_note,
    )


def get_entry_types_for_student_category(
    category: str | None,
) -> tuple[str, ...] | None:
    """Return underlying LedgerEntry.EntryType values for a student category, or None if invalid."""
    if not category:
        return None
    return _STUDENT_CATEGORY_ENTRY_TYPES.get(category.strip().upper())


_TENANT_MANUAL_PREFIX_RE = re.compile(r"tenant_\d+:manual_")
_BOOKING_REF_RE = re.compile(r"^booking_(\d+)$")
_BOOKING_DESC_RE = re.compile(r"booking\s*#(\d+)", re.IGNORECASE)
_INTERNAL_AUDIT_TOKEN_RE = re.compile(
    r"(tenant_\d+:manual_|manual top-up:|by (?:user:\d+|system)|\(\s*(?:consume|release):)",
    re.IGNORECASE,
)

_BOOKING_DESCRIPTION_TEMPLATES: dict[str, str] = {
    LedgerEntry.EntryType.RESERVE: "Lesson Hold",
    LedgerEntry.EntryType.RELEASE: "Lesson Hold Released",
    LedgerEntry.EntryType.CONSUME: "Lesson Settlement",
    LedgerEntry.EntryType.WAIVER: "Penalty Waiver Credit",
}

_STATIC_STUDENT_DESCRIPTIONS: dict[str, str] = {
    LedgerEntry.EntryType.ADJUSTMENT_CREDIT: "Account Balance Adjustment",
    LedgerEntry.EntryType.ADJUSTMENT_DEBIT: "Account Balance Adjustment",
    LedgerEntry.EntryType.PAYOUT_HOLD: "Refund Request Hold",
    LedgerEntry.EntryType.PAYOUT_RELEASE: "Refund Hold Released",
    LedgerEntry.EntryType.PAYOUT_SETTLE: "Refund Completed",
}


def _extract_booking_id(entry: LedgerEntry) -> str | None:
    ref_match = _BOOKING_REF_RE.match((entry.reference_id or "").strip())
    if ref_match:
        return ref_match.group(1)
    desc_match = _BOOKING_DESC_RE.search(entry.description or "")
    if desc_match:
        return desc_match.group(1)
    return None


def format_student_ledger_description(entry: LedgerEntry) -> str:
    """
    Return a student-safe, human-readable description for a LedgerEntry without
    exposing internal audit tokens (actor IDs, settlement codes, or tenant prefixes).
    """
    entry_type = entry.entry_type
    raw_desc = (entry.description or "").strip()

    if entry_type in _STATIC_STUDENT_DESCRIPTIONS:
        return _STATIC_STUDENT_DESCRIPTIONS[entry_type]

    if entry_type in _BOOKING_DESCRIPTION_TEMPLATES:
        base_label = _BOOKING_DESCRIPTION_TEMPLATES[entry_type]
        booking_id = _extract_booking_id(entry)
        label = f"{base_label} for Booking #{booking_id}" if booking_id else base_label
        if (
            entry_type
            in (
                LedgerEntry.EntryType.CONSUME,
                LedgerEntry.EntryType.RELEASE,
            )
            and "DISPUTE_" in raw_desc
        ):
            return f"{label} (Dispute Resolved)"
        return label

    if entry_type == LedgerEntry.EntryType.CREDIT:
        if not raw_desc or _INTERNAL_AUDIT_TOKEN_RE.search(raw_desc):
            return "Wallet Deposit Credit"
        return raw_desc

    return _TENANT_MANUAL_PREFIX_RE.sub("", raw_desc) or "Wallet Transaction"


def attach_student_ledger_display_fields(
    entries: list[LedgerEntry],
    viewer_tz: ZoneInfo,
) -> None:
    """
    Attach localized timestamps, 5-category badge metadata, signed available-balance
    net impact, and post-entry available balance snapshot for student ledger rendering.
    """
    for entry in entries:
        local_dt: datetime | None = (
            timezone.localtime(entry.created_at, viewer_tz).replace(tzinfo=None)
            if entry.created_at is not None
            else None
        )
        entry.local_created_at = local_dt
        entry.student_display = classify_student_ledger_entry(
            entry_type=entry.entry_type,
            amount=entry.amount,
        )
        entry.available_after = (entry.settled_after - entry.reserved_after).quantize(
            Decimal("0.01")
        )
        entry.student_description = format_student_ledger_description(entry)


class StudentWalletSnapshot(NamedTuple):
    tenant: "Tenant | None"
    wallet: Wallet | None
    available_balance: Decimal
    reserved_balance: Decimal
    settled_balance: Decimal
    currency: str
    is_frozen: bool


def get_student_wallet_snapshot(
    user: "User",
    *,
    request=None,
) -> StudentWalletSnapshot:
    """
    Read-only lookup of a student's wallet balances for the active storefront tenant.
    Caches on request (when provided) to avoid duplicate queries between view and context processor.
    Returns zero balances without creating a Wallet row when none exists.
    """
    if request is not None:
        cached = getattr(request, "_student_wallet_snapshot", None)
        if cached is not None:
            return cached

    from accounts.legal import get_default_tenant

    tenant = get_default_tenant()
    wallet = None
    if tenant is not None:
        wallet = Wallet.objects.filter(
            tenant=tenant,
            user=user,
            currency=tenant.default_currency,
        ).first()

    if wallet is None:
        snapshot = StudentWalletSnapshot(
            tenant=tenant,
            wallet=None,
            available_balance=Decimal("0.00"),
            reserved_balance=Decimal("0.00"),
            settled_balance=Decimal("0.00"),
            currency=tenant.default_currency if tenant is not None else "USD",
            is_frozen=False,
        )
    else:
        snapshot = StudentWalletSnapshot(
            tenant=tenant,
            wallet=wallet,
            available_balance=wallet.available_balance,
            reserved_balance=wallet.reserved_balance,
            settled_balance=wallet.settled_balance,
            currency=wallet.currency,
            is_frozen=wallet.is_frozen,
        )

    if request is not None:
        request._student_wallet_snapshot = snapshot
    return snapshot


class ExpectedBalances(NamedTuple):
    expected_settled: Decimal
    expected_reserved: Decimal


def compute_expected_balances(wallet: Wallet) -> ExpectedBalances:
    """
    Computes expected settled and reserved balances directly from the wallet's immutable LedgerEntries.
    Uses database-level conditional aggregation for speed and precision.
    """
    aggregates = wallet.entries.aggregate(
        settled_inc=Sum(
            Case(
                When(entry_type__in=SETTLED_INCREMENT_ENTRY_TYPES, then=F("amount")),
                default=Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        ),
        settled_dec=Sum(
            Case(
                When(entry_type__in=SETTLED_DECREMENT_ENTRY_TYPES, then=F("amount")),
                default=Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        ),
        reserved_inc=Sum(
            Case(
                When(entry_type__in=RESERVED_INCREMENT_ENTRY_TYPES, then=F("amount")),
                default=Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        ),
        reserved_dec=Sum(
            Case(
                When(entry_type__in=RESERVED_DECREMENT_ENTRY_TYPES, then=F("amount")),
                default=Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        ),
    )

    settled = (aggregates["settled_inc"] or Decimal("0.00")) - (
        aggregates["settled_dec"] or Decimal("0.00")
    )
    reserved = (aggregates["reserved_inc"] or Decimal("0.00")) - (
        aggregates["reserved_dec"] or Decimal("0.00")
    )

    return ExpectedBalances(
        expected_settled=settled.quantize(Decimal("0.01")),
        expected_reserved=reserved.quantize(Decimal("0.01")),
    )


def audit_wallet(*, wallet: Wallet) -> dict:
    """
    Audits a single wallet's cached balance against its immutable ledger entries.
    Returns a structured dictionary detailing balances and discrepancies.
    """
    breakdown = compute_expected_balances(wallet)
    settled_diff = (wallet.settled_balance - breakdown.expected_settled).quantize(
        Decimal("0.01")
    )
    reserved_diff = (wallet.reserved_balance - breakdown.expected_reserved).quantize(
        Decimal("0.01")
    )
    has_discrepancy = (settled_diff != Decimal("0.00")) or (
        reserved_diff != Decimal("0.00")
    )

    return {
        "wallet_id": wallet.id,
        "user_id": wallet.user_id,
        "tenant_id": wallet.tenant_id,
        "currency": wallet.currency,
        "is_frozen": wallet.is_frozen,
        "cached_settled": wallet.settled_balance,
        "expected_settled": breakdown.expected_settled,
        "cached_reserved": wallet.reserved_balance,
        "expected_reserved": breakdown.expected_reserved,
        "settled_discrepancy": settled_diff,
        "reserved_discrepancy": reserved_diff,
        "has_discrepancy": has_discrepancy,
    }


def reconcile_wallet_balances(*, tenant: "Tenant | None" = None) -> list[dict]:
    """
    Scans active wallets and computes discrepancies between cached snapshots and ledger entries.
    Emits a CRITICAL log entry for any detected mismatch.
    Returns a list of audit discrepancy records.
    """
    wallets = Wallet.objects.filter(user__is_active=True).select_related(
        "user", "tenant"
    )
    if tenant is not None:
        wallets = wallets.filter(tenant=tenant)

    discrepancies: list[dict] = []
    for wallet in wallets:
        audit_res = audit_wallet(wallet=wallet)
        if audit_res["has_discrepancy"]:
            logger.critical(
                "Wallet balance discrepancy detected: wallet_id=%s tenant_id=%s user_id=%s "
                "cached_settled=%s expected_settled=%s cached_reserved=%s expected_reserved=%s",
                wallet.id,
                wallet.tenant_id,
                wallet.user_id,
                audit_res["cached_settled"],
                audit_res["expected_settled"],
                audit_res["cached_reserved"],
                audit_res["expected_reserved"],
            )
            discrepancies.append(audit_res)

    return discrepancies


def freeze_wallet(*, wallet_id: int, is_frozen: bool) -> Wallet:
    """
    Updates the is_frozen state of a wallet atomically under select_for_update lock.
    Emits a structured audit log and returns the updated Wallet instance.
    """
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
        wallet.is_frozen = is_frozen
        wallet.save(update_fields=["is_frozen", "updated_at"])

        logger.info(
            "Wallet frozen state updated: wallet_id=%s is_frozen=%s",
            wallet.id,
            is_frozen,
        )
        return wallet


def repair_wallet_cache(
    *,
    wallet_id: int,
    admin_user: "User | None" = None,
    commit: bool = False,
) -> dict:
    """
    Audits and optionally repairs a wallet's cached balance from its immutable ledger entries.
    When commit=True, updates the wallet snapshot under select_for_update row lock and emits an audit log.
    """
    with transaction.atomic():
        locked_wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
        audit_res = audit_wallet(wallet=locked_wallet)

        old_settled = locked_wallet.settled_balance
        old_reserved = locked_wallet.reserved_balance
        expected_settled = audit_res["expected_settled"]
        expected_reserved = audit_res["expected_reserved"]
        has_discrepancy = audit_res["has_discrepancy"]

        if commit and has_discrepancy:
            locked_wallet.settled_balance = expected_settled
            locked_wallet.reserved_balance = expected_reserved
            locked_wallet.save(
                update_fields=["settled_balance", "reserved_balance", "updated_at"]
            )

            logger.info(
                "Wallet balance cache repaired: wallet_id=%s admin_id=%s "
                "settled_before=%s settled_after=%s reserved_before=%s reserved_after=%s",
                locked_wallet.id,
                admin_user.id if admin_user else None,
                old_settled,
                expected_settled,
                old_reserved,
                expected_reserved,
            )

        return {
            "wallet_id": wallet_id,
            "admin_id": admin_user.id if admin_user else None,
            "committed": commit,
            "has_discrepancy": has_discrepancy,
            "settled_before": old_settled,
            "settled_after": (expected_settled if commit else old_settled),
            "reserved_before": old_reserved,
            "reserved_after": (expected_reserved if commit else old_reserved),
            "expected_settled": expected_settled,
            "expected_reserved": expected_reserved,
        }


def _validate_positive_decimal(amount: Decimal) -> None:
    if not isinstance(amount, Decimal) or amount <= Decimal("0.00"):
        raise InvalidAmountError(
            f"Amount must be a positive Decimal, got {amount!r} ({type(amount).__name__})"
        )


def _lock_wallet(tenant: "Tenant", wallet: Wallet) -> Wallet:
    try:
        return Wallet.objects.select_for_update().get(pk=wallet.pk, tenant=tenant)
    except Wallet.DoesNotExist:
        raise TenantMismatchError(
            f"Wallet {wallet.pk} does not belong to tenant {tenant.pk} or does not exist."
        )


def _sync_wallet_instance(wallet: Wallet, locked_wallet: Wallet) -> None:
    wallet.settled_balance = locked_wallet.settled_balance
    wallet.reserved_balance = locked_wallet.reserved_balance
    wallet.updated_at = locked_wallet.updated_at


def get_or_create_wallet(
    tenant: "Tenant",
    user: "User",
    currency: str = "USD",
) -> Wallet:
    """
    Idempotently retrieves or creates a Wallet for the given tenant, user, and currency.
    Safely handles concurrent creations via an inner savepoint and database constraint fallback.
    """
    try:
        with transaction.atomic():
            wallet, _ = Wallet.objects.get_or_create(
                tenant=tenant,
                user=user,
                currency=currency,
            )
            return wallet
    except IntegrityError:
        return Wallet.objects.get(
            tenant=tenant,
            user=user,
            currency=currency,
        )


def credit_wallet(
    *,
    tenant: "Tenant",
    wallet: Wallet,
    amount: Decimal,
    idempotency_key: str,
    reference_id: str,
    description: str,
    entry_type: str = LedgerEntry.EntryType.CREDIT,
) -> LedgerEntry:
    """
    Credits a wallet's settled_balance atomically under select_for_update lock.
    Creates an immutable LedgerEntry audit record and pushes a live balance update
    to the wallet owner's private Channels group upon transaction commit.
    Allowed even if wallet.is_frozen is True.
    """
    _validate_positive_decimal(amount)

    if entry_type not in ALLOWED_CREDIT_ENTRY_TYPES:
        raise InvalidEntryTypeError(
            f"Entry type {entry_type!r} is not allowed for credit_wallet. "
            f"Allowed: {sorted(ALLOWED_CREDIT_ENTRY_TYPES)}"
        )

    with transaction.atomic():
        locked_wallet = _lock_wallet(tenant, wallet)

        locked_wallet.settled_balance += amount
        locked_wallet.save(update_fields=["settled_balance", "updated_at"])

        entry = LedgerEntry.objects.create(
            tenant=tenant,
            wallet=locked_wallet,
            entry_type=entry_type,
            amount=amount,
            settled_after=locked_wallet.settled_balance,
            reserved_after=locked_wallet.reserved_balance,
            idempotency_key=idempotency_key,
            reference_id=reference_id,
            description=description,
        )

        schedule_wallet_balance_updated(
            locked_wallet,
            entry_id=entry.id,
            entry_type=entry_type,
            amount=amount,
        )

        _sync_wallet_instance(wallet, locked_wallet)
        return entry


def reserve_funds(
    *,
    tenant: "Tenant",
    wallet: Wallet,
    amount: Decimal,
    idempotency_key: str,
    reference_id: str,
    description: str,
) -> LedgerEntry:
    """
    Reserves funds from a wallet's available_balance atomically under select_for_update lock.
    Increments reserved_balance and creates an immutable RESERVE LedgerEntry.
    Rejects if wallet is frozen or available_balance is insufficient.
    """
    _validate_positive_decimal(amount)

    with transaction.atomic():
        locked_wallet = _lock_wallet(tenant, wallet)

        if locked_wallet.is_frozen:
            raise WalletFrozenError(
                f"Wallet {locked_wallet.pk} is frozen; cannot reserve funds."
            )

        if locked_wallet.available_balance < amount:
            raise InsufficientBalanceError(
                f"Insufficient available balance: required {amount}, "
                f"available {locked_wallet.available_balance}."
            )

        locked_wallet.reserved_balance += amount
        locked_wallet.save(update_fields=["reserved_balance", "updated_at"])

        entry = LedgerEntry.objects.create(
            tenant=tenant,
            wallet=locked_wallet,
            entry_type=LedgerEntry.EntryType.RESERVE,
            amount=amount,
            settled_after=locked_wallet.settled_balance,
            reserved_after=locked_wallet.reserved_balance,
            idempotency_key=idempotency_key,
            reference_id=reference_id,
            description=description,
        )

        _sync_wallet_instance(wallet, locked_wallet)
        return entry


def adjust_wallet(
    *,
    tenant: "Tenant",
    wallet: Wallet,
    amount: Decimal,
    direction: str,
    reason: str,
    admin_user: "User",
) -> LedgerEntry:
    """
    Executes an administrative balance adjustment (credit or debit) under select_for_update lock.
    Enforces strict direction, amount, reason, and tenant validations.
    Emits a structured security audit log and creates an immutable LedgerEntry.
    """
    if direction not in ("credit", "debit"):
        raise ValueError(
            f"Invalid direction {direction!r}. Must be 'credit' or 'debit'."
        )

    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Reason must be a non-empty string.")

    _validate_positive_decimal(amount)

    if wallet.tenant_id != tenant.id:
        raise TenantMismatchError(
            f"Wallet {wallet.pk} does not belong to tenant {tenant.pk}."
        )

    with transaction.atomic():
        locked_wallet = _lock_wallet(tenant, wallet)

        if direction == "credit":
            locked_wallet.settled_balance += amount
            entry_type = LedgerEntry.EntryType.ADJUSTMENT_CREDIT
        else:  # direction == "debit"
            if locked_wallet.available_balance < amount:
                raise InsufficientBalanceError(
                    f"Insufficient available balance: required {amount}, "
                    f"available {locked_wallet.available_balance}."
                )
            locked_wallet.settled_balance -= amount
            entry_type = LedgerEntry.EntryType.ADJUSTMENT_DEBIT

        locked_wallet.save(update_fields=["settled_balance", "updated_at"])

        idempotency_key = (
            f"tenant_{tenant.id}:adjust_{locked_wallet.id}:{uuid.uuid4().hex}"
        )
        reference_id = f"admin_{admin_user.id}"
        description = f"Administrative adjustment ({direction}): {reason.strip()}"

        entry = LedgerEntry.objects.create(
            tenant=tenant,
            wallet=locked_wallet,
            entry_type=entry_type,
            amount=amount,
            settled_after=locked_wallet.settled_balance,
            reserved_after=locked_wallet.reserved_balance,
            idempotency_key=idempotency_key,
            reference_id=reference_id,
            description=description,
        )

        if direction == "credit":
            schedule_wallet_balance_updated(
                locked_wallet,
                entry_id=entry.id,
                entry_type=entry_type,
                amount=amount,
            )

        _sync_wallet_instance(wallet, locked_wallet)

        logger.info(
            "Administrative wallet adjustment: wallet_id=%s admin_id=%s direction=%s amount=%s reason=%r entry_id=%s",
            locked_wallet.id,
            admin_user.id,
            direction,
            amount,
            reason.strip(),
            entry.id,
        )

        return entry


def _lock_booking_wallet(tenant: "Tenant", student_id: int) -> Wallet:
    locked_wallet = (
        Wallet.objects.select_for_update()
        .filter(
            tenant=tenant,
            user_id=student_id,
            currency=tenant.default_currency,
        )
        .first()
    )
    if locked_wallet is None:
        mismatched = Wallet.objects.filter(tenant=tenant, user_id=student_id).first()
        if mismatched is not None:
            raise CurrencyMismatchError(
                f"Wallet currency {mismatched.currency} does not match "
                f"tenant default currency {tenant.default_currency}."
            )
        raise Wallet.DoesNotExist(
            f"No wallet found for user {student_id} in tenant {tenant.pk}."
        )
    return locked_wallet


def _lock_tenant_booking(tenant: "Tenant", booking: "Booking"):
    from booking.models import Booking
    from portfolio.models import TeacherProfile

    locked_teacher = TeacherProfile.objects.select_for_update().get(
        pk=booking.teacher_id
    )
    if tenant.teacher_profile_id != locked_teacher.pk:
        raise TenantMismatchError(
            f"Booking {booking.pk} does not belong to tenant {tenant.pk}."
        )
    locked_booking = Booking.objects.select_for_update().get(pk=booking.pk)
    return locked_teacher, locked_booking


def settle_booking(
    *,
    tenant: "Tenant",
    booking: "Booking",
    action: str,
    actor: "User | None" = None,
    reason: str,
) -> LedgerEntry | None:
    """
    Executes the terminal financial settlement of a booking hold under strict lock ordering:
    TeacherProfile -> Booking -> Wallet.

    Returns None idempotently if the booking's payment_status is not RESERVED after locking.
    Permits settling existing holds even when the student's wallet is frozen.
    """
    from booking.models import Booking

    if action not in ("consume", "release"):
        raise ValueError(
            f"Invalid settlement action {action!r}. Must be 'consume' or 'release'."
        )

    if reason not in Booking.SettlementReason.values:
        raise ValueError(
            f"Invalid settlement reason {reason!r}. Must be a valid Booking.SettlementReason."
        )

    with transaction.atomic():
        _, locked_booking = _lock_tenant_booking(tenant, booking)
        if locked_booking.payment_status != Booking.PaymentStatus.RESERVED:
            booking.payment_status = locked_booking.payment_status
            booking.settlement_reason = locked_booking.settlement_reason
            return None

        amount = locked_booking.held_amount
        _validate_positive_decimal(amount)

        locked_wallet = _lock_booking_wallet(tenant, locked_booking.student_id)

        if action == "release":
            if locked_wallet.reserved_balance < amount:
                raise InsufficientBalanceError(
                    f"Insufficient reserved balance to release {amount}: "
                    f"reserved={locked_wallet.reserved_balance}."
                )
            locked_wallet.reserved_balance -= amount
            locked_wallet.save(update_fields=["reserved_balance", "updated_at"])
            entry_type = LedgerEntry.EntryType.RELEASE
            new_payment_status = Booking.PaymentStatus.RELEASED
        else:
            if (
                locked_wallet.reserved_balance < amount
                or locked_wallet.settled_balance < amount
            ):
                raise InsufficientBalanceError(
                    f"Insufficient balance to consume {amount}: "
                    f"settled={locked_wallet.settled_balance}, "
                    f"reserved={locked_wallet.reserved_balance}."
                )
            locked_wallet.settled_balance -= amount
            locked_wallet.reserved_balance -= amount
            locked_wallet.save(
                update_fields=["settled_balance", "reserved_balance", "updated_at"]
            )
            entry_type = LedgerEntry.EntryType.CONSUME
            new_payment_status = Booking.PaymentStatus.CONSUMED

        locked_booking.payment_status = new_payment_status
        locked_booking.settlement_reason = reason
        locked_booking.save(
            update_fields=["payment_status", "settlement_reason", "updated_at"]
        )

        actor_label = f"user:{actor.id}" if actor is not None else "system"
        entry = LedgerEntry.objects.create(
            tenant=tenant,
            wallet=locked_wallet,
            entry_type=entry_type,
            amount=amount,
            settled_after=locked_wallet.settled_balance,
            reserved_after=locked_wallet.reserved_balance,
            idempotency_key=f"tenant:{tenant.id}:booking:{locked_booking.id}:settle",
            reference_id=f"booking_{locked_booking.id}",
            description=(
                f"Booking settlement ({action}: {reason}) by {actor_label} "
                f"for booking #{locked_booking.id}"
            ),
        )

        booking.payment_status = locked_booking.payment_status
        booking.settlement_reason = locked_booking.settlement_reason
        booking.updated_at = locked_booking.updated_at
        return entry


def waive_penalty(
    *,
    tenant: "Tenant",
    booking: "Booking",
    teacher_user: "User",
    reason: str = "Goodwill fee waiver",
) -> LedgerEntry:
    """
    Issues a single-use compensatory WAIVER ledger entry restoring a consumed
    late-cancellation or student-absence fee under TeacherProfile -> Booking -> Wallet locks.
    """
    from booking.models import Booking

    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("Waiver reason must be a non-empty string.")

    waivable_reasons = (
        Booking.SettlementReason.LATE_CANCEL,
        Booking.SettlementReason.STUDENT_ABSENT,
    )

    with transaction.atomic():
        locked_teacher, locked_booking = _lock_tenant_booking(tenant, booking)

        if locked_teacher.user_id != teacher_user.id:
            raise ValidationError(
                f"User {teacher_user.id} is not authorized to waive penalties for tenant {tenant.pk}."
            )

        if (
            locked_booking.settlement_reason not in waivable_reasons
            or locked_booking.payment_status != Booking.PaymentStatus.CONSUMED
        ):
            raise ValidationError(
                "Penalty waiver is only permitted for consumed LATE_CANCEL or STUDENT_ABSENT bookings."
            )

        amount = locked_booking.held_amount
        _validate_positive_decimal(amount)

        locked_wallet = _lock_booking_wallet(tenant, locked_booking.student_id)
        locked_wallet.settled_balance += amount
        locked_wallet.save(update_fields=["settled_balance", "updated_at"])

        description = (
            f"Penalty waiver for booking #{locked_booking.id}: {reason.strip()}"
        )[:255]
        entry = LedgerEntry.objects.create(
            tenant=tenant,
            wallet=locked_wallet,
            entry_type=LedgerEntry.EntryType.WAIVER,
            amount=amount,
            settled_after=locked_wallet.settled_balance,
            reserved_after=locked_wallet.reserved_balance,
            idempotency_key=f"tenant:{tenant.id}:booking:{locked_booking.id}:waiver",
            reference_id=f"booking_{locked_booking.id}",
            description=description,
        )

        schedule_wallet_balance_updated(
            locked_wallet,
            entry_id=entry.id,
            entry_type=LedgerEntry.EntryType.WAIVER,
            amount=amount,
        )

        logger.info(
            "Penalty waived: tenant_id=%s booking_id=%s teacher_user_id=%s amount=%s entry_id=%s",
            tenant.id,
            locked_booking.id,
            teacher_user.id,
            amount,
            entry.id,
        )
        return entry
