from datetime import date, time
from django.test import TestCase
from django.core.exceptions import ValidationError
from accounts.models import User
from portfolio.models import TeacherProfile
from booking.models import AvailabilitySlot, Booking


class BookingModelTests(TestCase):
    def setUp(self):
        # Create teacher user and profile
        self.teacher_user = User.objects.create_user(
            username='testteacher', password='test123', role=User.Role.TEACHER
        )
        self.teacher = TeacherProfile.objects.create(user=self.teacher_user)

        # Create student user
        self.student_user = User.objects.create_user(
            username='teststudent', password='test123', role=User.Role.STUDENT
        )

        # Create an available slot
        self.slot = AvailabilitySlot.objects.create(
            teacher=self.teacher,
            date=date(2026, 9, 25),
            start_time=time(10, 0),
            end_time=time(11, 0),
            is_booked=False,
        )


    # 1. Valid booking creation and slot sync
    def test_valid_booking_creation_syncs_slot(self):
        """Test creating a valid booking sets slot.is_booked to True."""
        booking = Booking(
            student=self.student_user,
            slot=self.slot,
            lesson_type=Booking.LessonType.REGULAR,
            status=Booking.Status.PENDING,
        )
        booking.save()

        self.assertEqual(Booking.objects.count(), 1)
        # Refresh slot from DB to check is_booked status
        self.slot.refresh_from_db()
        self.assertTrue(self.slot.is_booked)


    #2. Prevent a teacher from booking their own slot
    def test_teacher_cannot_book_own_slot(self):
        """Test ValidationError when a teacher tries to book their own slot."""
        booking = Booking(
            student=self.teacher_user,  # Teacher trying to book their own slot
            slot=self.slot,
            lesson_type=Booking.LessonType.REGULAR,
            status=Booking.Status.PENDING,
        )
        with self.assertRaises(ValidationError):
            booking.save()


    # 3. Cancelling a booking frees the slot
    def test_cancelling_booking_frees_slot(self):
        """Test that updating booking status to CANCELLED sets slot.is_booked back to False."""
        booking = Booking.objects.create(
            student=self.student_user,
            slot=self.slot,
        )
        self.slot.refresh_from_db()
        self.assertTrue(self.slot.is_booked)

        # Cancel booking
        booking.status = Booking.Status.CANCELLED
        booking.save()

        self.slot.refresh_from_db()
        self.assertFalse(self.slot.is_booked)

    # 4. Cannot book an already booked slot
    def test_cannot_book_already_booked_slot(self):
        """Test ValidationError when trying to book a slot that is already booked."""
        # First booking
        Booking.objects.create(
            student=self.student_user,
            slot=self.slot,
        )

        # Second student trying to book the same slot
        another_student = User.objects.create_user(
            username='student2', password='password123'
        )
        second_booking = Booking(
            student=another_student,
            slot=self.slot,
        )
        with self.assertRaises(ValidationError):
            second_booking.save()            