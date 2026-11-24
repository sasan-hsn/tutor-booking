from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import StudentProfile, Tenant, TermsAcceptance, User


class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (("Role", {"fields": ("role",)}),)
    list_display = BaseUserAdmin.list_display + ("role",)


class ReadOnlyModelAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(User, UserAdmin)
admin.site.register(StudentProfile)
admin.site.register(Tenant, ReadOnlyModelAdmin)
admin.site.register(TermsAcceptance, ReadOnlyModelAdmin)
