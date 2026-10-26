from datetime import timedelta
from zoneinfo import ZoneInfo
from django.urls import reverse
from django.utils import timezone
from accounts.tests.base import RoleTestCase
from booking.models import Booking


class LessonRequestsModalContentTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile
        # Ensure teacher timezone is known (Asia/Tehran is UTC+3:30)
        self.teacher_user.timezone = "Asia/Tehran"
        self.teacher_user.save()
        self.tz = ZoneInfo("Asia/Tehran")

        # Future relative dates from now so expire_stale_bookings never marks them expired.
        now_teacher = timezone.localtime(timezone.now(), self.tz)
        sat_date = self._get_next_weekday_date(now_teacher, target_weekday=5)
        sun_date = sat_date + timedelta(days=1)
        mon_date = sat_date + timedelta(days=2)

        dt_start_regular = timezone.datetime.combine(
            sat_date, timezone.datetime.min.time(), tzinfo=self.tz
        ).replace(hour=14, minute=0)
        dt_end_regular = dt_start_regular + timedelta(minutes=50)

        # Pending Regular Lesson (50m) on Saturday
        self.pending_regular = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=dt_start_regular,
            end_at=dt_end_regular,
            lesson_type=Booking.LessonType.REGULAR,
            duration_minutes=50,
            status=Booking.Status.PENDING,
        )

        # Pending Trial Lesson (25m) on Sunday
        dt_start_trial = timezone.datetime.combine(
            sun_date, timezone.datetime.min.time(), tzinfo=self.tz
        ).replace(hour=16, minute=0)
        dt_end_trial = dt_start_trial + timedelta(minutes=25)

        self.pending_trial = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=dt_start_trial,
            end_at=dt_end_trial,
            lesson_type=Booking.LessonType.TRIAL,
            duration_minutes=25,
            status=Booking.Status.PENDING,
        )

        # Cancellation Request on Monday
        dt_start_cancel = timezone.datetime.combine(
            mon_date, timezone.datetime.min.time(), tzinfo=self.tz
        ).replace(hour=11, minute=0)
        dt_end_cancel = dt_start_cancel + timedelta(minutes=50)

        self.cancellation_request = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=dt_start_cancel,
            end_at=dt_end_cancel,
            lesson_type=Booking.LessonType.REGULAR,
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
            cancellation_requested=True,
        )

        self.sat_expected = f"Sat, {sat_date.strftime('%b %d')}, 14:00–14:50"
        self.sun_expected = f"Sun, {sun_date.strftime('%b %d')}, 16:00–16:25"
        self.mon_expected = f"Mon, {mon_date.strftime('%b %d')}, 11:00–11:50"

    def _get_next_weekday_date(self, current_dt, target_weekday):
        """Calculate the next future date for target_weekday (0=Mon...6=Sun), at least 2 days out."""
        days_ahead = (target_weekday - current_dt.weekday()) % 7
        if days_ahead <= 1:
            days_ahead += 7
        return (current_dt + timedelta(days=days_ahead)).date()

    def test_pending_regular_request_displays_weekday_date_and_badges(self):
        """A pending regular request displays formatted weekday + date, type badge, and duration badge."""
        response = self.teacher_client.get(reverse('booking:teacher_lesson_requests'))
        self.assertEqual(response.status_code, 200)

        # Date and time formatting: Sat, Oct 31, 14:00–14:50
        self.assertContains(response, self.sat_expected)
        # Lesson type badge
        self.assertContains(response, '<span class="badge-lesson-type">Regular Lesson</span>')
        # Duration badge
        self.assertContains(response, '<span class="badge-duration">50 min</span>')

    def test_pending_trial_request_displays_weekday_date_and_badges(self):
        """A pending trial request displays formatted weekday + date, type badge, and duration badge."""
        response = self.teacher_client.get(reverse('booking:teacher_lesson_requests'))
        self.assertEqual(response.status_code, 200)

        # Date and time formatting: Sun, Nov 01, 16:00–16:25
        self.assertContains(response, self.sun_expected)
        # Lesson type badge
        self.assertContains(response, '<span class="badge-lesson-type">Trial Lesson</span>')
        # Duration badge
        self.assertContains(response, '<span class="badge-duration">25 min</span>')

    def test_cancellation_request_displays_weekday_date_and_badges(self):
        """A cancellation request displays formatted weekday + date, type badge, and duration badge."""
        response = self.teacher_client.get(reverse('booking:teacher_lesson_requests'))
        self.assertEqual(response.status_code, 200)

        # Date and time formatting: Mon, Nov 02, 11:00–11:50
        self.assertContains(response, self.mon_expected)
        # Lesson type badge
        self.assertContains(response, '<span class="badge-lesson-type">Regular Lesson</span>')
        # Duration badge
        self.assertContains(response, '<span class="badge-duration">50 min</span>')
        # Cancellation badge
        self.assertContains(response, '<span class="request-badge">Cancellation</span>')

    def test_date_formatting_respects_teacher_timezone_midnight_crossover(self):
        """Requests crossing midnight relative to UTC reflect the teacher's local calendar day and weekday."""
        now_teacher = timezone.localtime(timezone.now(), self.tz)
        fri_date = self._get_next_weekday_date(now_teacher, target_weekday=4)
        sat_date = fri_date + timedelta(days=1)

        # Friday 22:30 UTC -> in Asia/Tehran (UTC+3:30) it is Saturday 02:00
        utc_dt = timezone.datetime.combine(
            fri_date, timezone.datetime.min.time(), tzinfo=ZoneInfo("UTC")
        ).replace(hour=22, minute=30)

        midnight_booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=utc_dt,
            end_at=utc_dt + timedelta(minutes=25),
            lesson_type=Booking.LessonType.TRIAL,
            duration_minutes=25,
            status=Booking.Status.PENDING,
        )

        response = self.teacher_client.get(reverse('booking:teacher_lesson_requests'))
        self.assertEqual(response.status_code, 200)

        expected_crossover = f"Sat, {sat_date.strftime('%b %d')}, 02:00–02:25"
        self.assertContains(response, expected_crossover)
