from django.contrib import admin

from tutor_booking.admin import ReadOnlyModelAdmin

from .models import LedgerEntry, Wallet


@admin.register(Wallet)
class WalletAdmin(ReadOnlyModelAdmin):
    list_display = (
        "id",
        "tenant",
        "user",
        "currency",
        "settled_balance",
        "reserved_balance",
        "is_frozen",
        "updated_at",
    )
    search_fields = ("user__username", "user__email", "tenant__name")
    list_filter = ("currency", "is_frozen")


@admin.register(LedgerEntry)
class LedgerEntryAdmin(ReadOnlyModelAdmin):
    list_display = (
        "id",
        "tenant",
        "wallet",
        "entry_type",
        "amount",
        "settled_after",
        "reserved_after",
        "reference_id",
        "created_at",
    )
    search_fields = (
        "idempotency_key",
        "reference_id",
        "description",
        "wallet__user__username",
        "tenant__name",
    )
    list_filter = ("entry_type", "tenant")
    ordering = ("-created_at", "-id")
