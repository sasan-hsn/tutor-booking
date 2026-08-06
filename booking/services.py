from datetime import datetime, timedelta
from django.db import transaction

from .models import AvailabilitySlot, RegularAvailability, WeeklyOverride


@transaction.atomic
def generate_slots_for_teacher(teacher, start_date, end_date):
    """Generates availability slots for a teacher within a date range (inclusive),

    respecting WeeklyOverrides and RegularAvailability rules.
    """
    duration = timedelta(minutes=teacher.lesson_duration_minutes)
    created_slots = []

    current_date = start_date
    while current_date <= end_date:
        # 1. Check for WeeklyOverride on current_date
        overrides = WeeklyOverride.objects.filter(
            teacher=teacher, date=current_date
        )

        if overrides.exists():
            # If any override is full-day off (is_available=False), no slots generated for today
            is_full_day_off = overrides.filter(is_available=False).exists()
            if not is_full_day_off:
                # Process active override windows
                active_overrides = overrides.filter(is_available=True)
                for override in active_overrides:
                    _create_slots_in_window(
                        teacher=teacher,
                        date_val=current_date,
                        start_time=override.start_time,
                        end_time=override.end_time,
                        duration=duration,
                        created_slots=created_slots,
                    )
        else:
            # 2. Fall back to RegularAvailability for the weekday
            weekday = current_date.weekday()
            regular_rules = RegularAvailability.objects.filter(
                teacher=teacher, day_of_week=weekday
            )
            for rule in regular_rules:
                _create_slots_in_window(
                    teacher=teacher,
                    date_val=current_date,
                    start_time=rule.start_time,
                    end_time=rule.end_time,
                    duration=duration,
                    created_slots=created_slots,
                )

        current_date += timedelta(days=1)

    return created_slots


def _create_slots_in_window(
    teacher, date_val, start_time, end_time, duration, created_slots
):
    """Helper function to slice a time window into slots based on lesson_duration_minutes."""
    current_dt = datetime.combine(date_val, start_time)
    end_dt = datetime.combine(date_val, end_time)

    while current_dt + duration <= end_dt:
        slot_start = current_dt.time()
        slot_end = (current_dt + duration).time()

        slot, created = AvailabilitySlot.objects.get_or_create(
            teacher=teacher,
            date=date_val,
            start_time=slot_start,
            defaults={'end_time': slot_end, 'is_booked': False},
        )

        if created:
            created_slots.append(slot)

        current_dt += duration