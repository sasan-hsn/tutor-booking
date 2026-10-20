from django.contrib import admin
from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'event_type', 'level', 'title', 'is_read', 'created_at')
    list_filter = ('event_type', 'level', 'is_read', 'created_at')
    search_fields = ('recipient__email', 'recipient__username', 'title', 'message')
    readonly_fields = ('created_at', 'read_at')
