from datetime import date, time
from django.test import TestCase
from django.core.exceptions import ValidationError
from accounts.models import User
from booking.models import Booking


class BookingModelTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username='testteacher', password='test123', role=User.Role.TEACHER
        )
        self.teacher = self.teacher_user.teacher_profile

        self.student_user = User.objects.create_user(
            username='teststudent', password='test123', role=User.Role.STUDENT
        )

        self.booking_date = date(2026, 9, 25)
        self.start_time = time(10, 0)
        self.end_time = time(11, 0)

    # 1. Valid booking creation
    def test_valid_booking_creation(self):
        """Test creating a valid booking succeeds."""
        booking = Booking(
            student=self.student_user,
            teacher=self.teacher,
            date=self.booking_date,
            start_time=self.start_time,
            end_time=self.end_time,
            lesson_type=Booking.LessonType.REGULAR,
            status=Booking.Status.PENDING,
        )
        booking.save()

        self.assertEqual(Booking.objects.count(), 1)

    # 2. Prevent a teacher from booking their own availability
    def test_teacher_cannot_book_own_availability(self):
        """Test ValidationError when a teacher tries to book themself as student."""
        booking = Booking(
            student=self.teacher_user,
            teacher=self.teacher,
            date=self.booking_date,
            start_time=self.start_time,
            end_time=self.end_time,
            lesson_type=Booking.LessonType.REGULAR,
            status=Booking.Status.PENDING,
        )
        with self.assertRaises(ValidationError):
            booking.save()

    # 3. Cancelling a booking doesn't error and status updates correctly
    def test_cancelling_booking_updates_status(self):
        """Test that updating a booking's status to CANCELLED succeeds."""
        booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            date=self.booking_date,
            start_time=self.start_time,
            end_time=self.end_time,
        )

        booking.status = Booking.Status.CANCELLED
        booking.save()

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)

    # 4. Cannot create an overlapping active booking for the same teacher/time
    def test_cannot_book_overlapping_time_for_same_teacher(self):
        """Test ValidationError when trying to book a time that overlaps an existing active booking."""
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            date=self.booking_date,
            start_time=self.start_time,
            end_time=self.end_time,
        )

        another_student = User.objects.create_user(
            username='student2', password='password123'
        )
        second_booking = Booking(
            student=another_student,
            teacher=self.teacher,
            date=self.booking_date,
            start_time=self.start_time,
            end_time=self.end_time,
        )
        with self.assertRaises(ValidationError):
            second_booking.save()

    # 5. A cancelled booking does NOT block a new overlapping booking
    def test_cancelled_booking_does_not_block_new_booking(self):
        """Test that a CANCELLED booking doesn't prevent a new booking at the same time."""
        first_booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            date=self.booking_date,
            start_time=self.start_time,
            end_time=self.end_time,
        )
        first_booking.status = Booking.Status.CANCELLED
        first_booking.save()

        another_student = User.objects.create_user(
            username='student2', password='password123'
        )
        second_booking = Booking(
            student=another_student,
            teacher=self.teacher,
            date=self.booking_date,
            start_time=self.start_time,
            end_time=self.end_time,
        )
        second_booking.save()  # should NOT raise

        self.assertEqual(Booking.objects.filter(status=Booking.Status.PENDING).count(), 1)