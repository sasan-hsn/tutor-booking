from django.contrib import admin, messages
from django.contrib.admin.actions import delete_selected as default_delete_selected
from django.contrib.admin.utils import unquote
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.urls import reverse

from .models import (
    Booking,
    RegularAvailability,
    Review,
    TeacherDailyDigestRecord,
    WeeklyOverride,
)

admin.site.register(RegularAvailability)
admin.site.register(WeeklyOverride)
admin.site.register(Review)


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "student",
        "teacher",
        "start_at",
        "status",
        "payment_status",
        "held_amount",
    )
    list_filter = ("status", "payment_status", "lesson_type")
    search_fields = ("student__username", "student__email", "teacher__user__username")
    actions = ["delete_selected_bookings"]

    def get_actions(self, request):
        actions = super().get_actions(request)
        if "delete_selected" in actions:
            actions["delete_selected"] = (
                BookingAdmin.delete_selected_bookings,
                "delete_selected",
                actions["delete_selected"][2],
            )
        actions.pop("delete_selected_bookings", None)
        return actions

    def delete_selected_bookings(self, request, queryset):
        if queryset.filter(payment_status=Booking.PaymentStatus.RESERVED).exists():
            self.message_user(
                request,
                Booking.ACTIVE_HOLD_DELETE_ERROR,
                level=messages.ERROR,
            )
            return None
        return default_delete_selected(self, request, queryset)

    def delete_view(self, request, object_id, extra_context=None):
        if request.method == "POST":
            obj = self.get_object(request, unquote(object_id))
            if obj is not None and obj.payment_status == Booking.PaymentStatus.RESERVED:
                self.message_user(
                    request,
                    Booking.ACTIVE_HOLD_DELETE_ERROR,
                    level=messages.ERROR,
                )
                return HttpResponseRedirect(
                    reverse(
                        f"admin:{self.opts.app_label}_{self.opts.model_name}_change",
                        args=[obj.pk],
                    )
                )
        return super().delete_view(request, object_id, extra_context=extra_context)

    def delete_model(self, request, obj):
        try:
            super().delete_model(request, obj)
        except ValidationError as exc:
            self.message_user(request, exc.message, level=messages.ERROR)

    def delete_queryset(self, request, queryset):
        try:
            super().delete_queryset(request, queryset)
        except ValidationError as exc:
            self.message_user(request, exc.message, level=messages.ERROR)


@admin.register(TeacherDailyDigestRecord)
class TeacherDailyDigestRecordAdmin(admin.ModelAdmin):
    list_display = ("teacher", "target_date", "booking_count", "status", "sent_at")
    list_filter = ("status", "target_date")
    search_fields = ("teacher__user__username", "teacher__user__email")
    readonly_fields = ("sent_at",)
