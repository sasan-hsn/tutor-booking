from datetime import date, time
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.test import TestCase

from portfolio.models import TeacherProfile
from booking.models import WeeklyOverride

User = get_user_model()


class WeeklyOverrideModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='test_teacher', password='password123'
        )
        self.teacher = TeacherProfile.objects.create(user=self.user)
        self.target_date = date(2026, 9, 20)

    # 1. Valid active slot (is_available=True)
    def test_valid_active_override_creation(self):
        """Test creating a valid active availability override."""
        override = WeeklyOverride(
            teacher=self.teacher,
            date=self.target_date,
            start_time=time(10, 0),
            end_time=time(14, 0),
            is_available=True,
        )
        override.full_clean()
        override.save()
        self.assertEqual(WeeklyOverride.objects.count(), 1)

    # 2. Valid full-day off (is_available=False without start/end times)
    def test_valid_full_day_off_creation(self):
        """Test creating a valid full-day off override."""
        override = WeeklyOverride(
            teacher=self.teacher,
            date=self.target_date,
            is_available=False,
        )
        override.full_clean()
        override.save()
        self.assertEqual(WeeklyOverride.objects.count(), 1)
        self.assertIsNone(override.start_time)
        self.assertIsNone(override.end_time)

    # 3. Error when is_available=True but start/end are missing
    def test_active_override_missing_times_raises_error(self):
        """Test ValidationError when an active slot is created without start or end time."""
        override = WeeklyOverride(
            teacher=self.teacher,
            date=self.target_date,
            is_available=True,
        )
        with self.assertRaises(ValidationError):
            override.full_clean()

    # 4. Error when is_available=False but start/end are provided
    def test_full_day_off_with_times_raises_error(self):
        """Test ValidationError when a day off override has start or end time defined."""
        override = WeeklyOverride(
            teacher=self.teacher,
            date=self.target_date,
            start_time=time(9, 0),
            end_time=time(12, 0),
            is_available=False,
        )
        with self.assertRaises(ValidationError):
            override.full_clean()

    # 5. Error when start_time >= end_time
    def test_start_time_after_or_equal_end_time_raises_error(self):
        """Test ValidationError when start_time is after or equal to end_time."""
        override = WeeklyOverride(
            teacher=self.teacher,
            date=self.target_date,
            start_time=time(16, 0),
            end_time=time(14, 0),
            is_available=True,
        )
        with self.assertRaises(ValidationError):
            override.full_clean()

    # 6. Error for overlapping active slots on the same date
    def test_overlapping_active_slots_raises_error(self):
        """Test ValidationError when active time slots overlap on the same date."""
        WeeklyOverride.objects.create(
            teacher=self.teacher,
            date=self.target_date,
            start_time=time(10, 0),
            end_time=time(14, 0),
            is_available=True,
        )

        overlapping_override = WeeklyOverride(
            teacher=self.teacher,
            date=self.target_date,
            start_time=time(12, 0),
            end_time=time(16, 0),
            is_available=True,
        )
        with self.assertRaises(ValidationError):
            overlapping_override.full_clean()

    # 7. Error when adding an active slot to a date already marked as day off
    def test_add_active_slot_when_day_off_exists_raises_error(self):
        """Test ValidationError when creating an active slot for a date already set to day off."""
        WeeklyOverride.objects.create(
            teacher=self.teacher,
            date=self.target_date,
            is_available=False,
        )

        active_override = WeeklyOverride(
            teacher=self.teacher,
            date=self.target_date,
            start_time=time(10, 0),
            end_time=time(12, 0),
            is_available=True,
        )
        with self.assertRaises(ValidationError):
            active_override.full_clean()

    # 8. Error when adding a second day off for the same date
    def test_duplicate_full_day_off_raises_error(self):
        """Test ValidationError when creating a second day off override for the same date."""
        WeeklyOverride.objects.create(
            teacher=self.teacher,
            date=self.target_date,
            is_available=False,
        )

        second_day_off = WeeklyOverride(
            teacher=self.teacher,
            date=self.target_date,
            is_available=False,
        )
        with self.assertRaises(ValidationError):
            second_day_off.full_clean()