from django.contrib import admin
from .models import RegularAvailability, WeeklyOverride, Booking, Review, TeacherDailyDigestRecord

admin.site.register(RegularAvailability)
admin.site.register(WeeklyOverride)
admin.site.register(Booking)
admin.site.register(Review)


@admin.register(TeacherDailyDigestRecord)
class TeacherDailyDigestRecordAdmin(admin.ModelAdmin):
    list_display = ('teacher', 'target_date', 'booking_count', 'status', 'sent_at')
    list_filter = ('status', 'target_date')
    search_fields = ('teacher__user__username', 'teacher__user__email')
    readonly_fields = ('sent_at',)
