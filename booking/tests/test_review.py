from datetime import datetime
from zoneinfo import ZoneInfo
from django.test import TestCase
from django.core.exceptions import ValidationError
from accounts.models import User
from booking.models import Booking, Review
from django.db import IntegrityError


class ReviewModelTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(username='testteacher', password='test123', role=User.Role.TEACHER)
        self.teacher = self.teacher_user.teacher_profile

        self.student_user = User.objects.create_user(username='teststudent', password='test123', role=User.Role.STUDENT)

        tz = ZoneInfo(self.teacher_user.timezone)
        self.booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=datetime(2026, 6, 15, 14, 0, tzinfo=tz),
            end_at=datetime(2026, 6, 15, 15, 0, tzinfo=tz),
            status=Booking.Status.COMPLETED,
        )

    # 1. Valid review creation
    def test_review_creation(self):
        """Test that a review can be created for a completed booking."""
        review = Review.objects.create(
            student=self.student_user,
            booking=self.booking,
            rating=5,
            comment="Great lesson!"
        )
        self.assertEqual(review.booking, self.booking)
        self.assertEqual(review.rating, 5)
        self.assertEqual(review.comment, "Great lesson!")

    # 2. Review requires completed booking
    def test_review_requires_completed_booking(self):
        """Test that a review can only be created for a completed booking."""
        tz = ZoneInfo(self.teacher_user.timezone)
        pending_booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=datetime(2026, 6, 16, 10, 0, tzinfo=tz),
            end_at=datetime(2026, 6, 16, 11, 0, tzinfo=tz),
            status=Booking.Status.PENDING,
        )

        with self.assertRaises(ValidationError):
            Review.objects.create(
                student=self.student_user,
                booking=pending_booking,
                rating=4,
                comment="Good lesson!"
            )

    # 3. Another student cannot review someone else's booking
    def test_another_student_cannot_review(self):
        """Test that a student cannot create a review for a booking they did not make."""
        another_student = User.objects.create_user(username='anotherstudent', password='test123', role=User.Role.STUDENT)
        with self.assertRaises(ValidationError):
            Review.objects.create(
                student=another_student,
                booking=self.booking,
                rating=3,
                comment="Not my lesson!"
            )

    # 4. Cannot create duplicate reviews for the same booking
    def test_duplicate_review_for_same_booking_raises_error(self):
        """Test that a booking cannot have more than one review due to OneToOne relationship."""
        Review.objects.create(
            student=self.student_user,
            booking=self.booking,
            rating=5,
        )

        with self.assertRaises((ValidationError, IntegrityError)):
            Review.objects.create(
                student=self.student_user,
                booking=self.booking,
                rating=4,
            )