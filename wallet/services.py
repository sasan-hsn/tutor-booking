from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import IntegrityError, transaction

from wallet.exceptions import (
    InsufficientBalanceError,
    InvalidAmountError,
    InvalidEntryTypeError,
    TenantMismatchError,
    WalletFrozenError,
)
from wallet.models import LedgerEntry, Wallet

if TYPE_CHECKING:
    from accounts.models import Tenant, User

ALLOWED_CREDIT_ENTRY_TYPES = frozenset(
    {
        LedgerEntry.EntryType.CREDIT,
        LedgerEntry.EntryType.WAIVER,
        LedgerEntry.EntryType.ADJUSTMENT_CREDIT,
    }
)


def get_or_create_wallet(
    tenant: "Tenant",
    user: "User",
    currency: str = "USD",
) -> Wallet:
    """
    Idempotently retrieves or creates a Wallet for the given tenant, user, and currency.
    Safely handles concurrent creations via get_or_create and database constraint fallback.
    """
    try:
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
    Creates an immutable LedgerEntry audit record.
    Allowed even if wallet.is_frozen is True.
    """
    if not isinstance(amount, Decimal) or amount <= Decimal("0.00"):
        raise InvalidAmountError(
            f"Amount must be a positive Decimal, got {amount!r} ({type(amount).__name__})"
        )

    if entry_type not in ALLOWED_CREDIT_ENTRY_TYPES:
        raise InvalidEntryTypeError(
            f"Entry type {entry_type!r} is not allowed for credit_wallet. "
            f"Allowed: {sorted(ALLOWED_CREDIT_ENTRY_TYPES)}"
        )

    with transaction.atomic():
        try:
            locked_wallet = Wallet.objects.select_for_update().get(
                pk=wallet.pk, tenant=tenant
            )
        except Wallet.DoesNotExist:
            raise TenantMismatchError(
                f"Wallet {wallet.pk} does not belong to tenant {tenant.pk} or does not exist."
            )

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

        wallet.settled_balance = locked_wallet.settled_balance
        wallet.updated_at = locked_wallet.updated_at
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
    if not isinstance(amount, Decimal) or amount <= Decimal("0.00"):
        raise InvalidAmountError(
            f"Amount must be a positive Decimal, got {amount!r} ({type(amount).__name__})"
        )

    with transaction.atomic():
        try:
            locked_wallet = Wallet.objects.select_for_update().get(
                pk=wallet.pk, tenant=tenant
            )
        except Wallet.DoesNotExist:
            raise TenantMismatchError(
                f"Wallet {wallet.pk} does not belong to tenant {tenant.pk} or does not exist."
            )

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

        wallet.reserved_balance = locked_wallet.reserved_balance
        wallet.updated_at = locked_wallet.updated_at
        return entry
