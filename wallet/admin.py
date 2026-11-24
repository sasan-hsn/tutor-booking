from django.contrib import admin

from .models import Wallet


class ReadOnlyModelAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
