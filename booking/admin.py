from django.contrib import admin
from .models import RegularAvailability, WeeklyOverride, Booking, Review

admin.site.register(RegularAvailability)
admin.site.register(WeeklyOverride)
admin.site.register(Booking)
admin.site.register(Review)