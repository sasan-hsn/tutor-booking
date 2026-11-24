from decimal import Decimal

from django.conf import settings
from django.db import models


class Wallet(models.Model):
    tenant = models.ForeignKey(
        "accounts.Tenant",
        on_delete=models.PROTECT,
        related_name="wallets",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="wallets",
    )
    currency = models.CharField(max_length=3, default="USD")
    settled_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    reserved_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    is_frozen = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "user", "currency"],
                name="unique_tenant_user_wallet",
            ),
            models.CheckConstraint(
                condition=models.Q(settled_balance__gte=Decimal("0.00")),
                name="wallet_settled_balance_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_balance__gte=Decimal("0.00")),
                name="wallet_reserved_balance_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(settled_balance__gte=models.F("reserved_balance")),
                name="wallet_settled_gte_reserved",
            ),
        ]

    @property
    def available_balance(self) -> Decimal:
        return self.settled_balance - self.reserved_balance

    def __str__(self):
        return (
            f"Wallet({self.user.username}, {self.currency}, "
            f"settled={self.settled_balance}, reserved={self.reserved_balance})"
        )


class LedgerEntry(models.Model):
    class EntryType(models.TextChoices):
        CREDIT = "CREDIT", "Credit"
        RESERVE = "RESERVE", "Reserve"
        RELEASE = "RELEASE", "Release"
        CONSUME = "CONSUME", "Consume"
        WAIVER = "WAIVER", "Waiver"
        PAYOUT_HOLD = "PAYOUT_HOLD", "Payout Hold"
        PAYOUT_SETTLE = "PAYOUT_SETTLE", "Payout Settle"
        PAYOUT_RELEASE = "PAYOUT_RELEASE", "Payout Release"
        ADJUSTMENT_CREDIT = "ADJUSTMENT_CREDIT", "Adjustment Credit"
        ADJUSTMENT_DEBIT = "ADJUSTMENT_DEBIT", "Adjustment Debit"

    tenant = models.ForeignKey(
        "accounts.Tenant",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        related_name="entries",
    )
    entry_type = models.CharField(
        max_length=24,
        choices=EntryType.choices,
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    settled_after = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    reserved_after = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    idempotency_key = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
    )
    reference_id = models.CharField(
        max_length=128,
        db_index=True,
    )
    description = models.CharField(
        max_length=255,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")),
                name="ledger_entry_amount_positive",
            ),
        ]

    def __str__(self):
        return f"LedgerEntry({self.id}, {self.entry_type}, {self.amount})"
