from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo
from django.test import TestCase
from accounts.models import User
from booking.models import Booking


class BookingJoinWindowModelTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username='testteacher', password='testpassword123', role=User.Role.TEACHER
        )
        self.teacher = self.teacher_user.teacher_profile
        self.student_user = User.objects.create_user(
            username='teststudent', password='testpassword123', role=User.Role.STUDENT
        )
        tz = ZoneInfo(self.teacher_user.timezone)
        self.start_at = datetime(2026, 10, 10, 14, 0, tzinfo=tz)
        self.end_at = datetime(2026, 10, 10, 14, 50, tzinfo=tz)

        self.booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=self.start_at,
            end_at=self.end_at,
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
        )

    def test_join_window_constants(self):
        """Authoritative pre-start and post-end window constants must be 10 minutes."""
        self.assertEqual(Booking.JOIN_WINDOW_PRE_START_MINUTES, 10)
        self.assertEqual(Booking.JOIN_WINDOW_POST_END_MINUTES, 10)

    def test_join_window_start_and_end_properties(self):
        """join_window_starts_at and join_window_ends_at compute the 10-minute bounds."""
        expected_start = self.start_at - timedelta(minutes=10)
        expected_end = self.end_at + timedelta(minutes=10)
        self.assertEqual(self.booking.join_window_starts_at, expected_start)
        self.assertEqual(self.booking.join_window_ends_at, expected_end)

    def test_pre_start_boundary_conditions(self):
        """is_joinable is True from start_at - 10m onward; is_live is False before start_at."""
        # 11 minutes before start: outside window
        with patch('django.utils.timezone.now', return_value=self.start_at - timedelta(minutes=11)):
            self.assertFalse(self.booking.is_joinable)
            self.assertFalse(self.booking.is_live)

        # 10 minutes before start: exactly at window start
        with patch('django.utils.timezone.now', return_value=self.start_at - timedelta(minutes=10)):
            self.assertTrue(self.booking.is_joinable)
            self.assertFalse(self.booking.is_live)

        # 5 minutes before start: inside pre-start window
        with patch('django.utils.timezone.now', return_value=self.start_at - timedelta(minutes=5)):
            self.assertTrue(self.booking.is_joinable)
            self.assertFalse(self.booking.is_live)

    def test_during_lesson_conditions(self):
        """During the scheduled lesson interval, is_joinable is True and is_live is True."""
        # At start_at
        with patch('django.utils.timezone.now', return_value=self.start_at):
            self.assertTrue(self.booking.is_joinable)
            self.assertTrue(self.booking.is_live)

        # Mid-lesson (25 minutes in)
        with patch('django.utils.timezone.now', return_value=self.start_at + timedelta(minutes=25)):
            self.assertTrue(self.booking.is_joinable)
            self.assertTrue(self.booking.is_live)

        # 1 second before end_at
        with patch('django.utils.timezone.now', return_value=self.end_at - timedelta(seconds=1)):
            self.assertTrue(self.booking.is_joinable)
            self.assertTrue(self.booking.is_live)

    def test_post_end_boundary_conditions(self):
        """is_joinable remains True until end_at + 10m; is_live becomes False at end_at."""
        # Exactly at end_at: joinable, but no longer live (is_live: start_at <= now < end_at)
        with patch('django.utils.timezone.now', return_value=self.end_at):
            self.assertTrue(self.booking.is_joinable)
            self.assertFalse(self.booking.is_live)

        # 5 minutes after end_at: in post-end grace period
        with patch('django.utils.timezone.now', return_value=self.end_at + timedelta(minutes=5)):
            self.assertTrue(self.booking.is_joinable)
            self.assertFalse(self.booking.is_live)

        # 10 minutes after end_at: exactly at window close
        with patch('django.utils.timezone.now', return_value=self.end_at + timedelta(minutes=10)):
            self.assertTrue(self.booking.is_joinable)
            self.assertFalse(self.booking.is_live)

        # 11 minutes after end_at: window has closed
        with patch('django.utils.timezone.now', return_value=self.end_at + timedelta(minutes=11)):
            self.assertFalse(self.booking.is_joinable)
            self.assertFalse(self.booking.is_live)

    def test_status_gating_for_joinable(self):
        """is_joinable evaluates to True ONLY when status is CONFIRMED, even during active time."""
        now_mid_lesson = self.start_at + timedelta(minutes=15)
        with patch('django.utils.timezone.now', return_value=now_mid_lesson):
            # CONFIRMED -> True
            self.booking.status = Booking.Status.CONFIRMED
            self.booking.save()
            self.assertTrue(self.booking.is_joinable)

            # PENDING -> False
            self.booking.status = Booking.Status.PENDING
            self.booking.save()
            self.assertFalse(self.booking.is_joinable)

            # COMPLETED -> False
            self.booking.status = Booking.Status.COMPLETED
            self.booking.save()
            self.assertFalse(self.booking.is_joinable)

            # CANCELLED -> False
            self.booking.status = Booking.Status.CANCELLED
            self.booking.save()
            self.assertFalse(self.booking.is_joinable)

            # EXPIRED -> False
            self.booking.status = Booking.Status.EXPIRED
            self.booking.save()
            self.assertFalse(self.booking.is_joinable)

            # DISPUTING -> False
            self.booking.status = Booking.Status.DISPUTING
            self.booking.save()
            self.assertFalse(self.booking.is_joinable)
