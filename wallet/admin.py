from django.contrib import admin

from tutor_booking.admin import ReadOnlyModelAdmin

from .models import Wallet


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
