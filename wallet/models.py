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
