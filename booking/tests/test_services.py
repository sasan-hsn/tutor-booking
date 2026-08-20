from datetime import date, time
from django.contrib.auth import get_user_model
from django.test import TestCase

from portfolio.models import TeacherProfile
from booking.models import RegularAvailability, WeeklyOverride
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
        self.assertEqual(start_times, [time(10, 0), time(10, 30), time(11, 0)])

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
            date=self.target_monday,
            start_time=time(10, 30),
            end_time=time(11, 30),
            status=Booking.Status.CONFIRMED,
        )

        start_times = get_available_start_times(self.teacher, self.target_monday, duration_minutes=60)

        # 10:00 would end at 11:00 -> overlaps booking (10:30-11:30) -> excluded
        # 10:30 exactly matches booking -> excluded
        # 11:00 would end at 12:00 -> overlaps booking (ends 11:30) -> excluded
        self.assertEqual(start_times, [])