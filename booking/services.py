import calendar
from zoneinfo import ZoneInfo
from collections import defaultdict
from datetime import datetime, timedelta, date, time
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



INSTANT_TUTORING_BUFFER = timedelta(hours=1)

def get_available_start_times(teacher, date_val, duration_minutes):
    windows = get_availability_windows(teacher, date_val)
    if not windows:
        return []

    teacher_tz = ZoneInfo(teacher.user.timezone)
    duration = timedelta(minutes=duration_minutes)
    now = timezone.now()
    teacher_today = timezone.localtime(now, teacher_tz).date()

    if date_val == teacher_today:
        if not teacher.instant_tutoring_enabled:
            # Same-day bookings are closed entirely.
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
    )
    booked_ranges = [(b.start_at, b.end_at) for b in existing_bookings]

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



def get_calendar_grid(year, month, *, teacher=None, student=None):
    """Generate a monthly calendar grid (Sun-Sat) with pre-fetched bookings.

    Returns a matrix of weeks, where each day contains date metadata,
    active month flags, and associated booking records.
    """

    if teacher is not None:
        viewer_tz = ZoneInfo(teacher.user.timezone)
    elif student is not None:
        viewer_tz = ZoneInfo(student.timezone)
    else:
        raise ValueError("Must provide either teacher or student.")

    _, last_day = calendar.monthrange(year, month)
    month_start_local = date(year, month, 1)
    month_end_local = date(year, month, last_day)

    # Convert the local calendar month boundaries into aware UTC instants,
    # so the DB query correctly captures every booking that falls within
    # this month AS SEEN BY THE VIEWER (not as seen in UTC).
    range_start = datetime.combine(month_start_local, time.min, tzinfo=viewer_tz)
    range_end = datetime.combine(month_end_local, time.max, tzinfo=viewer_tz)

    if teacher is not None:
        bookings = Booking.objects.filter(
            teacher=teacher,
            start_at__lt=range_end,
            end_at__gt=range_start,
        ).select_related("student").order_by("start_at")

    else:
        bookings = Booking.objects.filter(
            student=student,
            start_at__lt=range_end,
            end_at__gt=range_start,
        ).select_related("teacher__user").order_by("start_at")

    bookings_by_date = defaultdict(list)
    for booking in bookings:
        local_date = timezone.localtime(booking.start_at, viewer_tz).date()
        bookings_by_date[local_date].append(booking)

    cal = calendar.Calendar(firstweekday=6)
    month_matrix = cal.monthdatescalendar(year, month)
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


def get_week_data(teacher, student_tz, week_days, duration_minutes):
    """For each day in week_days (dates in the student's local calendar),
    returns available slots as dicts with an aware `start_at` (for
    booking) and student-local `local_start_time`/`local_end_time` (for
    display). Handles timezone-offset spillover by checking the
    teacher's day before/after each requested day.
    """
    week_data = []
    for day in week_days:
        candidate_slots = []
        for teacher_day in (day - timedelta(days=1), day, day + timedelta(days=1)):
            candidate_slots.extend(get_available_start_times(teacher, teacher_day, duration_minutes))

        day_slots = []
        for slot_start in sorted(set(candidate_slots)):
            local_start = timezone.localtime(slot_start, student_tz)
            if local_start.date() != day:
                continue
            local_end = timezone.localtime(
                slot_start + timedelta(minutes=duration_minutes), student_tz
            )
            day_slots.append({
                'start_at': slot_start,
                'local_start_time': local_start.time(),
                'local_end_time': local_end.time(),
            })

        week_data.append({'day': day, 'slots': day_slots})

    return week_data