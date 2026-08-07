from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, StudentProfile


class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Role', {'fields': ('role',)}),
    )
    list_display = BaseUserAdmin.list_display + ('role',)


admin.site.register(User, UserAdmin)
admin.site.register(StudentProfile)