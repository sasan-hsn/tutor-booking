from django.contrib import admin

from tutor_booking.admin import ReadOnlyModelAdmin

from .models import (
    Order,
    Payment,
    PaymentAddress,
    Payout,
    TenantPaymentAccount,
)


@admin.register(TenantPaymentAccount)
class TenantPaymentAccountAdmin(ReadOnlyModelAdmin):
    list_display = ("id", "tenant", "provider", "ingestion_enabled", "created_at")
    list_filter = ("provider", "ingestion_enabled")
    search_fields = ("tenant__name", "provider")


@admin.register(PaymentAddress)
class PaymentAddressAdmin(ReadOnlyModelAdmin):
    list_display = (
        "id",
        "tenant",
        "provider",
        "address",
        "status",
        "activates_at",
        "retired_at",
        "created_at",
    )
    list_filter = ("provider", "status")
    search_fields = ("address", "tenant__name")


@admin.register(Order)
class OrderAdmin(ReadOnlyModelAdmin):
    list_display = (
        "id",
        "tenant",
        "student",
        "amount",
        "currency",
        "status",
        "idempotency_key",
        "claimed_at",
        "claimed_txhash",
        "created_at",
        "expires_at",
    )
    list_filter = ("status", "currency")
    search_fields = (
        "idempotency_key",
        "claimed_txhash",
        "student__username",
        "tenant__name",
    )


@admin.register(Payment)
class PaymentAdmin(ReadOnlyModelAdmin):
    list_display = (
        "id",
        "external_id",
        "tenant",
        "order",
        "claimed_by",
        "provider",
        "gross_amount",
        "net_amount",
        "currency",
        "status",
        "created_at",
    )
    list_filter = ("status", "provider", "currency")
    search_fields = (
        "external_id",
        "claimed_by__username",
        "tenant__name",
    )


@admin.register(Payout)
class PayoutAdmin(ReadOnlyModelAdmin):
    list_display = (
        "id",
        "tenant",
        "student",
        "provider",
        "destination",
        "external_ref",
        "amount_net",
        "status",
        "created_at",
    )
    list_filter = ("status", "provider")
    search_fields = (
        "external_ref",
        "destination",
        "student__username",
        "tenant__name",
    )
