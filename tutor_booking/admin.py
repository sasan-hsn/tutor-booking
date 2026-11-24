from django.contrib import admin


class ReadOnlyModelAdmin(admin.ModelAdmin):
    """
    Base ModelAdmin enforcing read-only access by denying add, change, and delete permissions.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
