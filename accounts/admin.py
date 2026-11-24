from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from tutor_booking.admin import ReadOnlyModelAdmin

from .models import StudentProfile, Tenant, TermsAcceptance, User


class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (("Role", {"fields": ("role",)}),)
    list_display = BaseUserAdmin.list_display + ("role",)


admin.site.register(User, UserAdmin)
admin.site.register(StudentProfile)
admin.site.register(Tenant, ReadOnlyModelAdmin)
admin.site.register(TermsAcceptance, ReadOnlyModelAdmin)
