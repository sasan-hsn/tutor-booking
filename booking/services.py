import calendar
from collections import defaultdict
from datetime import datetime, timedelta, date
from django.utils import timezone

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



def get_calendar_grid(teacher, year, month):
    """Generate a monthly calendar grid (Sun-Sat) with pre-fetched teacher bookings.

    Returns a matrix of weeks, where each day contains date metadata,
    active month flags, and associated booking records.
    """

    _, last_day = calendar.monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    bookings = Booking.objects.filter(
        teacher=teacher,
        date__range=(start_date, end_date)
    ).select_related("student").order_by("start_time")

    bookings_by_date = defaultdict(list)
    for booking in bookings:
        booking.display_name = booking.student.first_name or booking.student.username
        bookings_by_date[booking.date].append(booking)

    cal = calendar.Calendar(firstweekday=6)
    month_matrix = cal.monthdatescalendar(year, month)
    today = timezone.localdate()

    calendar_grid = []
    for week in month_matrix:
        week_data = []
        for day_date in week:
            week_data.append({
                "date": day_date,
                "day": day_date.day,
                "is_current_month": day_date.month == month,
                "is_today": day_date == today,
                "bookings": bookings_by_date.get(day_date, []),
            })
        calendar_grid.append(week_data)

    return calendar_grid



def get_calendar_navigation(request, today):
    try:
        current_year = int(request.GET.get("year", today.year))
        current_month = int(request.GET.get("month", today.month))
        if not (1 <= current_month <= 12):
            raise ValueError
    except (ValueError, TypeError):
        current_year = today.year
        current_month = today.month

    if current_month == 1:
        prev_month = 12
        prev_year = current_year -1 
    else:
        prev_month = current_month - 1
        prev_year = current_year 

    if current_month == 12:
        next_month = 1
        next_year = current_year + 1
    else:
        next_month = current_month + 1
        next_year = current_year

    return {
        "current_year": current_year,
        "current_month": current_month,
        "month_label": date(current_year, current_month, 1).strftime("%B %Y"),
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
    }