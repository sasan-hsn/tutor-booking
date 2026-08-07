from datetime import date, time
from django.test import TestCase
from django.core.exceptions import ValidationError
from accounts.models import User
from portfolio.models import TeacherProfile
from booking.models import AvailabilitySlot, Booking, Review


class ReviewModelTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(username='testteacher', password='test123', role=User.Role.TEACHER )
        self.teacher = TeacherProfile.objects.create(user=self.teacher_user)

        self.student_user = User.objects.create_user(username='teststudent', password='test123', role=User.Role.STUDENT)

        self.slot = AvailabilitySlot.objects.create(
            teacher=self.teacher,
            date=date(2026, 6, 15),
            start_time=time(14, 0),
            end_time=time(15, 0),
        )

        self.booking = Booking.objects.create(
            student=self.student_user,
            slot=self.slot,
            status=Booking.Status.COMPLETED,
        )


    def test_review_creation(self):
        review = Review.objects.create(
            student=self.student_user,
            booking=self.booking,
            rating=5,
            comment="Great lesson!"
        )
        self.assertEqual(review.booking, self.booking)
        self.assertEqual(review.rating, 5)
        self.assertEqual(review.comment, "Great lesson!")

    def test_review_requires_completed_booking(self):
        # Create a booking that is not completed
        pending_booking = Booking.objects.create(
            student=self.student_user,
            slot=AvailabilitySlot.objects.create(
                teacher=self.teacher,
                date=date(2026, 6, 16),
                start_time=time(10, 0),
                end_time=time(11, 0),
            ),
            status=Booking.Status.PENDING,
        )

        with self.assertRaises(ValidationError):
            Review.objects.create(
                student=self.student_user,
                booking=pending_booking,
                rating=4,
                comment="Good lesson!"
            )


    def test_another_student_cannot_review(self):
        another_student= User.objects.create_user(username='anotherstudent', password='test123', role=User.Role.STUDENT)
        with self.assertRaises(ValidationError):
            Review.objects.create(
                student=another_student,
                booking=self.booking,
                rating=3,
                comment="Not my lesson!"
            )    