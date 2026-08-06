from datetime import time
from django.test import TestCase
from django.core.exceptions import ValidationError
from accounts.models import User
from portfolio.models import TeacherProfile
from booking.models import RegularAvailability


class RegularAvailabilityValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='teacher1', password='password123')
        self.teacher = TeacherProfile.objects.create(user=self.user)

    def test_valid_regular_availability_creation(self):
        """Test creating a valid regular availability slot."""
        availability = RegularAvailability(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(14, 0),
            end_time=time(18, 0),
        )
        # Should not raise any exception
        availability.full_clean()
        availability.save()
        self.assertEqual(RegularAvailability.objects.count(), 1)

    def test_invalid_start_end_time(self):
        """Test validation error when start_time >= end_time."""
        availability = RegularAvailability(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(18, 0),
            end_time=time(14, 0),
        )
        with self.assertRaises(ValidationError):
            availability.full_clean()

    def test_overlapping_regular_availability(self):
        """Test validation error for overlapping time slots on the same day."""
        # Existing slot: 14:00 - 18:00
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(14, 0),
            end_time=time(18, 0),
        )

        # Overlapping slot: 16:00 - 20:00
        new_availability = RegularAvailability(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(16, 0),
            end_time=time(20, 0),
        )
        with self.assertRaises(ValidationError):
            new_availability.full_clean()

    def test_update_existing_regular_availability(self):
        """Test that updating an existing slot does not conflict with itself."""
        availability = RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(14, 0),
            end_time=time(18, 0),
        )
        # Modifying time slightly without overlapping other slots
        availability.end_time = time(19, 0)
        availability.full_clean()
        availability.save()
        self.assertEqual(availability.end_time, time(19, 0))