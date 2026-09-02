from datetime import date, time, datetime, timedelta
from django.utils import timezone
from zoneinfo import ZoneInfo
from django.contrib.auth import get_user_model
from django.test import TestCase

from portfolio.models import TeacherProfile
from booking.models import RegularAvailability, WeeklyOverride, Booking
from booking.services import get_availability_windows, get_available_start_times

User = get_user_model()


class AvailabilityWindowsServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='teacher_test', password='password123'
        )
        self.teacher = TeacherProfile.objects.create(
            user=self.user,
            lesson_duration_minutes=60,
        )
        # 2026-09-07 is a Monday (weekday = 0)
        self.target_monday = date(2026, 9, 7)
        self.tz = ZoneInfo(self.user.timezone)

    def test_windows_from_regular_availability(self):
        """RegularAvailability applies when there's no override for that date."""
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )

        windows = get_availability_windows(self.teacher, self.target_monday)

        self.assertEqual(windows, [(time(10, 0), time(12, 0))])

    def test_active_override_replaces_regular_availability(self):
        """An active WeeklyOverride completely replaces RegularAvailability for that date."""
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        WeeklyOverride.objects.create(
            teacher=self.teacher,
            date=self.target_monday,
            start_time=time(14, 0),
            end_time=time(16, 0),
            is_available=True,
        )

        windows = get_availability_windows(self.teacher, self.target_monday)

        self.assertEqual(windows, [(time(14, 0), time(16, 0))])

    def test_full_day_off_override_returns_no_windows(self):
        """A full-day-off WeeklyOverride results in zero available windows."""
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        WeeklyOverride.objects.create(
            teacher=self.teacher,
            date=self.target_monday,
            is_available=False,
        )

        windows = get_availability_windows(self.teacher, self.target_monday)

        self.assertEqual(windows, [])

    def test_available_start_times_respects_duration(self):
        """Bookable start times are generated at 30-min steps and fit within the window."""
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )

        start_times = get_available_start_times(self.teacher, self.target_monday, duration_minutes=60)

        # With a 60-min lesson in a 10:00-12:00 window and 30-min steps,
        # valid starts are 10:00, 10:30, 11:00 (11:00+60min=12:00 fits exactly)
        expected = [
            datetime(2026, 9, 7, 10, 0, tzinfo=self.tz),
            datetime(2026, 9, 7, 10, 30, tzinfo=self.tz),
            datetime(2026, 9, 7, 11, 0, tzinfo=self.tz),
        ]
        self.assertEqual(start_times, expected)

    def test_available_start_times_excludes_booked_overlaps(self):
        """An existing booking removes any start time that would overlap it."""
        from booking.models import Booking

        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )

        student = User.objects.create_user(username='student_test', password='password123')
        Booking.objects.create(
            student=student,
            teacher=self.teacher,
            start_at=datetime(2026, 9, 7, 10, 30, tzinfo=self.tz),
            end_at=datetime(2026, 9, 7, 11, 30, tzinfo=self.tz),
            status=Booking.Status.CONFIRMED,
        )

        start_times = get_available_start_times(self.teacher, self.target_monday, duration_minutes=60)

        # 10:00 would end at 11:00 -> overlaps booking (10:30-11:30) -> excluded
        # 10:30 exactly matches booking -> excluded
        # 11:00 would end at 12:00 -> overlaps booking (ends 11:30) -> excluded
        self.assertEqual(start_times, [])


    def test_available_start_times_excludes_past_slots(self):
        """Start times earlier than 'now' must never be returned, even
        when they fall inside an otherwise-open availability window."""
        now = timezone.localtime(timezone.now(), self.tz)
        today = now.date()

        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=today.weekday(),
            start_time=(now - timedelta(hours=2)).time().replace(second=0, microsecond=0),
            end_time=(now + timedelta(hours=2)).time().replace(second=0, microsecond=0),
        )

        start_times = get_available_start_times(self.teacher, today, duration_minutes=30)

        self.assertTrue(all(st > now for st in start_times))
        # sanity check: some future slots should still be offered
        self.assertTrue(len(start_times) > 0)

    def test_completed_past_booking_does_not_reopen_its_slot(self):
        """A COMPLETED booking's original slot is in the past, so it must
        stay excluded — regardless of COMPLETED not being in the
        'occupied' status filter used for overlap checks."""
        yesterday = (timezone.localtime(timezone.now(), self.tz) - timedelta(days=1)).date()

        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=yesterday.weekday(),
            start_time=time(10, 0),
            end_time=time(12, 0),
        )

        student = User.objects.create_user(username='student_completed', password='password123')
        Booking.objects.create(
            student=student,
            teacher=self.teacher,
            start_at=datetime.combine(yesterday, time(10, 0), tzinfo=self.tz),
            end_at=datetime.combine(yesterday, time(11, 0), tzinfo=self.tz),
            status=Booking.Status.COMPLETED,
        )

        start_times = get_available_start_times(self.teacher, yesterday, duration_minutes=60)

        self.assertEqual(start_times, [])