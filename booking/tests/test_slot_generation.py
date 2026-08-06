from datetime import date, time
from django.contrib.auth import get_user_model
from django.test import TestCase

from portfolio.models import TeacherProfile
from booking.models import AvailabilitySlot, RegularAvailability, WeeklyOverride
from booking.services import generate_slots_for_teacher

User = get_user_model()


class GenerateSlotsServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='teacher_test', password='password123'
        )
        # Set duration to 60 minutes for predictable math in tests
        self.teacher = TeacherProfile.objects.create(
            user=self.user,
            lesson_duration_minutes=60,
        )
        # 2026-09-07 is a Monday (weekday = 0)
        self.target_monday = date(2026, 9, 7)

    # 1. Slot generation from RegularAvailability
    def test_generate_slots_from_regular_availability(self):
        """Test slot generation relying purely on RegularAvailability rules."""
        # Create regular schedule for Mondays: 10:00 to 12:00 (60-min slots -> 2 slots expected)
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )

        created_slots = generate_slots_for_teacher(
            teacher=self.teacher,
            start_date=self.target_monday,
            end_date=self.target_monday,
        )

        self.assertEqual(len(created_slots), 2)
        self.assertEqual(AvailabilitySlot.objects.count(), 2)
        self.assertEqual(created_slots[0].start_time, time(10, 0))
        self.assertEqual(created_slots[0].end_time, time(11, 0))
        self.assertEqual(created_slots[1].start_time, time(11, 0))
        self.assertEqual(created_slots[1].end_time, time(12, 0))

    # 2. Active WeeklyOverride takes precedence over RegularAvailability
    def test_active_override_precedence_over_regular_availability(self):
        """Test that active WeeklyOverride overrides regular rules for a specific date."""
        # Regular: Monday 10:00 - 12:00
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )

        # Override on target Monday: 14:00 - 16:00
        WeeklyOverride.objects.create(
            teacher=self.teacher,
            date=self.target_monday,
            start_time=time(14, 0),
            end_time=time(16, 0),
            is_available=True,
        )

        created_slots = generate_slots_for_teacher(
            teacher=self.teacher,
            start_date=self.target_monday,
            end_date=self.target_monday,
        )

        # Should generate 2 slots strictly from override (14-15 and 15-16), ignoring 10-12
        self.assertEqual(len(created_slots), 2)
        self.assertEqual(created_slots[0].start_time, time(14, 0))
        self.assertEqual(created_slots[1].start_time, time(15, 0))

    # 3. Full-day off WeeklyOverride generates zero slots
    def test_full_day_off_override_generates_no_slots(self):
        """Test that a full-day off override prevents any slot generation on that date."""
        # Regular: Monday 10:00 - 12:00
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )

        # Full-day off override on target Monday
        WeeklyOverride.objects.create(
            teacher=self.teacher,
            date=self.target_monday,
            is_available=False,
        )

        created_slots = generate_slots_for_teacher(
            teacher=self.teacher,
            start_date=self.target_monday,
            end_date=self.target_monday,
        )

        self.assertEqual(len(created_slots), 0)
        self.assertEqual(AvailabilitySlot.objects.count(), 0)

    # 4. Idempotency test
    def test_service_idempotency(self):
        """Test that running the generation service multiple times does not duplicate slots."""
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )

        # First run
        first_run_slots = generate_slots_for_teacher(
            teacher=self.teacher,
            start_date=self.target_monday,
            end_date=self.target_monday,
        )
        self.assertEqual(len(first_run_slots), 2)
        self.assertEqual(AvailabilitySlot.objects.count(), 2)

        # Second run (exact same range)
        second_run_slots = generate_slots_for_teacher(
            teacher=self.teacher,
            start_date=self.target_monday,
            end_date=self.target_monday,
        )

        # Second run should return 0 NEW created slots and database count stays 2
        self.assertEqual(len(second_run_slots), 0)
        self.assertEqual(AvailabilitySlot.objects.count(), 2)