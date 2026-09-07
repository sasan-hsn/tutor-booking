import calendar
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone

from .models import Booking, RegularAvailability, WeeklyOverride

INSTANT_TUTORING_BUFFER = timedelta(hours=1)


def get_availability_windows(teacher, date_val: date):
    """
    Returns a list of (start_time, end_time) tuples representing the
    teacher's available windows on date_val.

    If any WeeklyOverride exists for this date, only override
    windows apply (RegularAvailability is ignored); otherwise RegularAvailability
    for that weekday applies.
    """
    # Evaluate overrides in a single database query
    overrides = list(
        WeeklyOverride.objects.filter(teacher=teacher, date=date_val).order_by('start_time')
    )

    if overrides:
        # If marked entirely unavailable on that date, return no windows
        if any(not o.is_available for o in overrides):
            return []
        return [(o.start_time, o.end_time) for o in overrides if o.is_available]

    weekday = date_val.weekday()
    regular_rules = RegularAvailability.objects.filter(
        teacher=teacher, day_of_week=weekday
    ).order_by('start_time')

    return [(r.start_time, r.end_time) for r in regular_rules]


def get_available_start_times(teacher, date_val: date, duration_minutes: int):
    """
    Calculates candidate start times on date_val in teacher's local time,
    accounting for existing bookings, instant tutoring buffer, and slot duration.
    """
    windows = get_availability_windows(teacher, date_val)
    if not windows:
        return []

    teacher_tz = ZoneInfo(teacher.user.timezone)
    duration = timedelta(minutes=duration_minutes)
    now = timezone.now()
    teacher_today = timezone.localtime(now, teacher_tz).date()

    if date_val == teacher_today:
        if not teacher.instant_tutoring_enabled:
            # Same-day bookings are closed entirely
            return []
        threshold = now + INSTANT_TUTORING_BUFFER
    else:
        threshold = now

    aware_windows = [
        (
            datetime.combine(date_val, w_start, tzinfo=teacher_tz),
            datetime.combine(date_val, w_end, tzinfo=teacher_tz),
        )
        for w_start, w_end in windows
    ]

    day_range_start = min(w[0] for w in aware_windows)
    day_range_end = max(w[1] for w in aware_windows)

    existing_bookings = Booking.objects.filter(
        teacher=teacher,
        status__in=[Booking.Status.PENDING, Booking.Status.CONFIRMED],
        start_at__lt=day_range_end,
        end_at__gt=day_range_start,
    ).values_list('start_at', 'end_at')

    booked_ranges = list(existing_bookings)

    available_times = []
    step = timedelta(minutes=30)

    for window_start, window_end in aware_windows:
        current_dt = window_start

        while current_dt + duration <= window_end:
            candidate_end = current_dt + duration

            if current_dt > threshold:
                overlaps = any(
                    current_dt < b_end and candidate_end > b_start
                    for b_start, b_end in booked_ranges
                )
                if not overlaps:
                    available_times.append(current_dt)

            current_dt += step

    return sorted(set(available_times))


def get_lesson_type_and_price(teacher, student):
    """
    Returns (lesson_type, price, duration_minutes) for a given student.
    First-time students receive trial settings if offered by teacher.
    """
    has_previous_lesson = Booking.objects.filter(
        student=student,
        teacher=teacher,
        status__in=[Booking.Status.CONFIRMED, Booking.Status.COMPLETED],
    ).exists()

    if teacher.offers_trial and not has_previous_lesson:
        return Booking.LessonType.TRIAL, teacher.trial_price, teacher.trial_duration_minutes
    return Booking.LessonType.REGULAR, teacher.lesson_price, teacher.lesson_duration_minutes


def get_calendar_grid(year: int, month: int, *, teacher=None, student=None):
    """
    Generate a monthly calendar grid (Sun-Sat) with pre-fetched bookings.
    Captures the entire visible grid span (including visible edge days from adjacent months).
    """
    if teacher is not None:
        viewer_tz = ZoneInfo(teacher.user.timezone)
    elif student is not None:
        viewer_tz = ZoneInfo(student.timezone)
    else:
        raise ValueError("Must provide either teacher or student.")

    cal = calendar.Calendar(firstweekday=6)
    month_matrix = cal.monthdatescalendar(year, month)

    # Capture boundaries of the full visible grid (including adjacent month days)
    grid_start_date = month_matrix[0][0]
    grid_end_date = month_matrix[-1][-1]

    range_start = datetime.combine(grid_start_date, time.min, tzinfo=viewer_tz)
    range_end = datetime.combine(grid_end_date, time.max, tzinfo=viewer_tz)

    booking_filter = {
        'start_at__lt': range_end,
        'end_at__gt': range_start,
    }

    if teacher is not None:
        bookings = (
            Booking.objects.filter(teacher=teacher, **booking_filter)
            .select_related("student")
            .order_by("start_at")
        )
    else:
        bookings = (
            Booking.objects.filter(student=student, **booking_filter)
            .select_related("teacher__user")
            .order_by("start_at")
        )

    bookings_by_date = defaultdict(list)
    for booking in bookings:
        local_date = timezone.localtime(booking.start_at, viewer_tz).date()
        bookings_by_date[local_date].append(booking)

    today = timezone.localtime(timezone.now(), viewer_tz).date()

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


def get_calendar_navigation(request, today: date):
    """Parses year and month from GET query parameters and provides next/prev links."""
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
        prev_year = current_year - 1
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


def get_week_data(teacher, student_tz, week_days, duration_minutes: int):
    """
    For each day in week_days (dates in the student's local calendar),
    returns available slots as dicts with an aware `start_at` and
    student-local display times.

    Optimized: Uses a local memoization cache so teacher days aren't computed
    redundantly across day boundaries.
    """
    slots_cache = {}

    def fetch_slots_for_teacher_day(t_day):
        if t_day not in slots_cache:
            slots_cache[t_day] = get_available_start_times(teacher, t_day, duration_minutes)
        return slots_cache[t_day]

    duration_delta = timedelta(minutes=duration_minutes)
    week_data = []

    for day in week_days:
        candidate_slots = []
        for teacher_day in (day - timedelta(days=1), day, day + timedelta(days=1)):
            candidate_slots.extend(fetch_slots_for_teacher_day(teacher_day))

        day_slots = []
        for slot_start in sorted(set(candidate_slots)):
            local_start = timezone.localtime(slot_start, student_tz)
            if local_start.date() != day:
                continue
            local_end = timezone.localtime(slot_start + duration_delta, student_tz)
            day_slots.append({
                'start_at': slot_start,
                'local_start_time': local_start.time(),
                'local_end_time': local_end.time(),
            })

        week_data.append({'day': day, 'slots': day_slots})

    return week_data