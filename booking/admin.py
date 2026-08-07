from django.contrib import admin
from .models import RegularAvailability, WeeklyOverride, AvailabilitySlot, Booking, Review

admin.site.register(RegularAvailability)
admin.site.register(WeeklyOverride)
admin.site.register(AvailabilitySlot)
admin.site.register(Booking)
admin.site.register(Review)