from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class TenantPaymentAccount(models.Model):
    tenant = models.ForeignKey(
        "accounts.Tenant",
        on_delete=models.PROTECT,
        related_name="payment_accounts",
    )
    provider = models.CharField(max_length=32)
    ingestion_enabled = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "provider"],
                name="unique_tenant_provider_account",
            ),
        ]

    def __str__(self):
        return f"TenantPaymentAccount({self.tenant_id}, provider={self.provider})"


class PaymentAddress(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        PENDING_ACTIVATION = "PENDING_ACTIVATION", "Pending Activation"
        RETIRED = "RETIRED", "Retired"

    tenant = models.ForeignKey(
        "accounts.Tenant",
        on_delete=models.PROTECT,
        related_name="payment_addresses",
    )
    provider = models.CharField(max_length=32)
    address = models.CharField(max_length=128, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    activates_at = models.DateTimeField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "address"],
                name="unique_provider_address",
            ),
            models.UniqueConstraint(
                fields=["tenant", "provider"],
                condition=models.Q(status="ACTIVE"),
                name="unique_active_tenant_provider_address",
            ),
        ]

    def __str__(self):
        return f"PaymentAddress({self.provider}: {self.address} [{self.status}])"


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"

    tenant = models.ForeignKey(
        "accounts.Tenant",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    payment_instructions_snapshot = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(max_length=128, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    # Student claim fields
    claimed_at = models.DateTimeField(null=True, blank=True)
    claimed_txhash = models.CharField(max_length=128, blank=True)
    claimed_paid_at = models.DateTimeField(null=True, blank=True)

    def clean(self):
        super().clean()
        if self.amount is not None and self.tenant_id:
            if self.amount < self.tenant.min_topup_amount:
                raise ValidationError(
                    {
                        "amount": (
                            f"Amount must be at least {self.tenant.min_topup_amount} {self.currency}."
                        )
                    }
                )

    def __str__(self):
        return f"Order({self.id}, student={self.student_id}, amount={self.amount} {self.currency}, status={self.status})"


class Payment(models.Model):
    class Status(models.TextChoices):
        INITIATED = "INITIATED", "Initiated"
        PENDING_VERIFICATION = "PENDING_VERIFICATION", "Pending Verification"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"
        NEEDS_REVIEW = "NEEDS_REVIEW", "Needs Review"

    tenant = models.ForeignKey(
        "accounts.Tenant",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payments",
    )
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payments",
    )
    provider = models.CharField(max_length=32, default="manual")
    external_id = models.CharField(max_length=255, unique=True, db_index=True)
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    fee_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    net_amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.INITIATED,
    )
    raw_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Payment({self.external_id}, gross={self.gross_amount} {self.currency}, status={self.status})"


class Payout(models.Model):
    class Status(models.TextChoices):
        PENDING_CONFIRMATION = "PENDING_CONFIRMATION", "Pending Confirmation"
        REQUESTED = "REQUESTED", "Requested"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"
        EXPIRED = "EXPIRED", "Expired"

    tenant = models.ForeignKey(
        "accounts.Tenant",
        on_delete=models.PROTECT,
        related_name="payouts",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payouts",
    )
    provider = models.CharField(max_length=32, default="crypto_usdt")
    destination = models.CharField(max_length=128)
    external_ref = models.CharField(max_length=128, null=True, blank=True)
    amount_gross = models.DecimalField(max_digits=12, decimal_places=2)
    fee_deducted = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    amount_net = models.DecimalField(max_digits=12, decimal_places=2)
    confirmation_token_hash = models.CharField(max_length=64, blank=True, null=True)
    confirmation_expires_at = models.DateTimeField(null=True, blank=True)
    confirmation_used_at = models.DateTimeField(null=True, blank=True)
    reminder_48h_sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING_CONFIRMATION,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_ref"],
                condition=models.Q(external_ref__isnull=False),
                name="unique_payout_provider_external_ref",
            ),
        ]

    def __str__(self):
        return f"Payout({self.id}, student={self.student_id}, net={self.amount_net}, status={self.status})"
