from datetime import datetime, timedelta

from .models import RegularAvailability, WeeklyOverride, Booking


def get_availability_windows(teacher, date_val):
    """Returns a list of (start_time, end_time) tuples representing the
    teacher's available windows on date_val, applying the #28 "Replacing"
    rule: if any WeeklyOverride exists for this date, only override windows
    apply (RegularAvailability is ignored); otherwise RegularAvailability
    for that weekday applies.
    """
    overrides = WeeklyOverride.objects.filter(teacher=teacher, date=date_val)

    if overrides.exists():
        if overrides.filter(is_available=False).exists():
            return []
        return [
            (o.start_time, o.end_time)
            for o in overrides.filter(is_available=True)
        ]

    weekday = date_val.weekday()
    regular_rules = RegularAvailability.objects.filter(
        teacher=teacher, day_of_week=weekday
    )
    return [(r.start_time, r.end_time) for r in regular_rules]


def get_available_start_times(teacher, date_val, duration_minutes):
    """Returns a sorted list of bookable start times (datetime.time objects)
    on date_val for a lesson of the given duration, respecting availability
    windows and existing active bookings.
    """
    windows = get_availability_windows(teacher, date_val)
    if not windows:
        return []

    duration = timedelta(minutes=duration_minutes)

    existing_bookings = Booking.objects.filter(
        teacher=teacher,
        date=date_val,
        status__in=[Booking.Status.PENDING, Booking.Status.CONFIRMED],
    )
    booked_ranges = [
        (
            datetime.combine(date_val, b.start_time),
            datetime.combine(date_val, b.end_time),
        )
        for b in existing_bookings
    ]

    available_times = []
    step = timedelta(minutes=30)

    for window_start, window_end in windows:
        current_dt = datetime.combine(date_val, window_start)
        window_end_dt = datetime.combine(date_val, window_end)

        while current_dt + duration <= window_end_dt:
            candidate_end = current_dt + duration

            overlaps = any(
                current_dt < b_end and candidate_end > b_start
                for b_start, b_end in booked_ranges
            )

            if not overlaps:
                available_times.append(current_dt.time())

            current_dt += step

    return sorted(set(available_times))


def get_lesson_type_and_price(teacher, student):
    """Returns (lesson_type, price, duration_minutes) for a given
    student booking with this teacher."""
    has_previous_lesson = Booking.objects.filter(
        student=student,
        teacher=teacher,
        status__in=[Booking.Status.CONFIRMED, Booking.Status.COMPLETED],
    ).exists()

    if teacher.offers_trial and not has_previous_lesson:
        return Booking.LessonType.TRIAL, teacher.trial_price, teacher.trial_duration_minutes
    return Booking.LessonType.REGULAR, teacher.lesson_price, teacher.lesson_duration_minutes