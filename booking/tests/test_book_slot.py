from datetime import timedelta, time, date
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo
from django.urls import reverse
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from booking.models import Booking, RegularAvailability


class BookSlotViewTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.lesson_price = Decimal('20.00')
        cls.teacher.lesson_price_25 = Decimal('12.00')
        cls.teacher.lesson_duration_minutes = 50
        cls.teacher.offers_trial = True
        cls.teacher.trial_price = Decimal('5.00')
        cls.teacher.trial_duration_minutes = 25
        cls.teacher.save()

    def setUp(self):
        super().setUp()
        self.tz = ZoneInfo(self.teacher_user.timezone)
        # a future weekday with a wide-open availability window
        self.future_day = timezone.localdate() + timedelta(days=2)
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=self.future_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        self.valid_start = timezone.datetime.combine(
            self.future_day, time(10, 0), tzinfo=self.tz
        )

    def _book(self, client, start_at, lesson_option='trial'):
        data = {}
        if start_at is not None:
            data['start_at'] = start_at.isoformat() if hasattr(start_at, 'isoformat') else start_at
        if lesson_option is not None:
            data['lesson_option'] = lesson_option
        return client.post(reverse('booking:book_slot'), data)

    def test_successful_booking_creates_confirmed_booking(self):
        response = self._book(self.student_client, self.valid_start, lesson_option='trial')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Booking.objects.count(), 1)
        booking = Booking.objects.first()
        self.assertEqual(booking.student, self.student_user)
        self.assertEqual(booking.teacher, self.teacher)
        self.assertEqual(booking.start_at, self.valid_start)

    def test_first_time_student_books_trial_pricing_and_duration(self):
        response = self._book(self.student_client, self.valid_start, lesson_option='trial')
        self.assertEqual(response.status_code, 200)
        booking = Booking.objects.first()
        self.assertEqual(booking.lesson_type, Booking.LessonType.TRIAL)
        self.assertEqual(booking.price, self.teacher.trial_price)
        self.assertEqual(booking.duration_minutes, 25)
        self.assertEqual(response.json()['duration_minutes'], 25)

    def test_first_time_student_books_regular_25(self):
        response = self._book(self.student_client, self.valid_start, lesson_option='regular_25')
        self.assertEqual(response.status_code, 200)
        booking = Booking.objects.first()
        self.assertEqual(booking.lesson_type, Booking.LessonType.REGULAR)
        self.assertEqual(booking.price, self.teacher.lesson_price_25)
        self.assertEqual(booking.duration_minutes, 25)
        self.assertEqual(response.json()['duration_minutes'], 25)

    def test_first_time_student_books_regular_50(self):
        response = self._book(self.student_client, self.valid_start, lesson_option='regular_50')
        self.assertEqual(response.status_code, 200)
        booking = Booking.objects.first()
        self.assertEqual(booking.lesson_type, Booking.LessonType.REGULAR)
        self.assertEqual(booking.price, self.teacher.lesson_price)
        self.assertEqual(booking.duration_minutes, 50)
        self.assertEqual(response.json()['duration_minutes'], 50)

    def test_first_time_student_booking_regular_forfeits_trial_eligibility(self):
        # 1. Book regular lesson (25m)
        response1 = self._book(self.student_client, self.valid_start, lesson_option='regular_25')
        self.assertEqual(response1.status_code, 200)

        # 2. Try to book a trial lesson for another slot
        other_start = self.valid_start + timedelta(hours=2)
        response2 = self._book(self.student_client, other_start, lesson_option='trial')
        self.assertEqual(response2.status_code, 400)
        self.assertIn('not eligible for a trial', response2.json()['error'].lower())

    def test_returning_student_booking_trial_rejected(self):
        # simulate a previous completed lesson
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=timezone.now() - timedelta(days=10),
            end_at=timezone.now() - timedelta(days=10) + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )
        response = self._book(self.student_client, self.valid_start, lesson_option='trial')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not eligible for a trial', response.json()['error'].lower())

    def test_returning_student_can_book_regular_50(self):
        # simulate a previous completed lesson
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=timezone.now() - timedelta(days=10),
            end_at=timezone.now() - timedelta(days=10) + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )
        response = self._book(self.student_client, self.valid_start, lesson_option='regular_50')
        self.assertEqual(response.status_code, 200)
        booking = Booking.objects.get(start_at=self.valid_start)
        self.assertEqual(booking.lesson_type, Booking.LessonType.REGULAR)
        self.assertEqual(booking.price, self.teacher.lesson_price)
        self.assertEqual(booking.duration_minutes, 50)
        self.assertEqual(response.json()['duration_minutes'], 50)

    def test_returning_student_can_book_regular_25(self):
        # simulate a previous completed lesson
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=timezone.now() - timedelta(days=10),
            end_at=timezone.now() - timedelta(days=10) + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )
        response = self._book(self.student_client, self.valid_start, lesson_option='regular_25')
        self.assertEqual(response.status_code, 200)
        booking = Booking.objects.get(start_at=self.valid_start)
        self.assertEqual(booking.lesson_type, Booking.LessonType.REGULAR)
        self.assertEqual(booking.price, self.teacher.lesson_price_25)
        self.assertEqual(booking.duration_minutes, 25)
        self.assertEqual(response.json()['duration_minutes'], 25)

    def test_trial_when_teacher_does_not_offer_trial_returns_400(self):
        self.teacher.offers_trial = False
        self.teacher.save()

        response = self._book(self.student_client, self.valid_start, lesson_option='trial')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not offered', response.json()['error'].lower())

    def test_regular_25_when_pricing_not_configured_returns_400(self):
        self.teacher.lesson_price_25 = None
        self.teacher.save()

        response = self._book(self.student_client, self.valid_start, lesson_option='regular_25')
        self.assertEqual(response.status_code, 400)
        self.assertIn('pricing is not configured', response.json()['error'].lower())

    def test_missing_start_at_returns_400(self):
        response = self.student_client.post(
            reverse('booking:book_slot'),
            {'lesson_option': 'trial'},
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_lesson_option_returns_400(self):
        response = self.student_client.post(
            reverse('booking:book_slot'),
            {'start_at': self.valid_start.isoformat()},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('lesson_option is required', response.json()['error'])

    def test_invalid_lesson_option_returns_400(self):
        response = self._book(self.student_client, self.valid_start, lesson_option='invalid_option')
        self.assertEqual(response.status_code, 400)
        self.assertIn('invalid lesson option', response.json()['error'].lower())

    def test_invalid_start_at_format_returns_400(self):
        response = self.student_client.post(
            reverse('booking:book_slot'),
            {'start_at': 'not-a-date', 'lesson_option': 'trial'},
        )
        self.assertEqual(response.status_code, 400)

    def test_naive_start_at_returns_400(self):
        naive = timezone.datetime.combine(self.future_day, time(10, 0))
        response = self.student_client.post(
            reverse('booking:book_slot'),
            {'start_at': naive.isoformat(), 'lesson_option': 'trial'},
        )
        self.assertEqual(response.status_code, 400)

    def test_unavailable_slot_returns_409(self):
        past_start = timezone.now() - timedelta(days=1)
        response = self._book(self.student_client, past_start, lesson_option='trial')
        self.assertEqual(response.status_code, 409)

    def test_already_booked_slot_returns_409(self):
        self._book(self.student_client, self.valid_start, lesson_option='trial')

        from accounts.models import User
        another_student = User.objects.create_user(
            username='second_student',
            password='password123',
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        another_client = self.client_class()
        another_client.force_login(another_student)

        response = self._book(another_client, self.valid_start, lesson_option='trial')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Booking.objects.count(), 1)

    def test_unverified_student_cannot_book_slot(self):
        self.student_user.is_email_verified = False
        self.student_user.save()

        response = self._book(self.student_client, self.valid_start, lesson_option='trial')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {'error': 'email_unverified'})
        self.assertEqual(Booking.objects.count(), 0)

    def test_book_slot_with_unverified_teacher_returns_409(self):
        self.teacher_user.is_email_verified = False
        self.teacher_user.save()

        response = self._book(self.student_client, self.valid_start, lesson_option='trial')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Booking.objects.count(), 0)

    def test_teacher_cannot_book_own_slot(self):
        response = self.teacher_client.post(
            reverse('booking:book_slot'),
            {'start_at': self.valid_start.isoformat(), 'lesson_option': 'trial'},
        )
        self.assertEqual(response.status_code, 403)

    def test_booking_succeeds_even_if_notification_dispatch_fails(self):
        with patch('booking.tasks.send_booking_request_student_email_task.delay', side_effect=ConnectionError("Redis connection refused")):
            response = self._book(self.student_client, self.valid_start, lesson_option='trial')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Booking.objects.count(), 1)
        booking = Booking.objects.first()
        self.assertEqual(booking.student, self.student_user)
        self.assertEqual(booking.status, Booking.Status.PENDING)

    def test_unexpected_exception_in_book_slot_returns_clean_500_json(self):
        with patch('booking.views.resolve_lesson_option', side_effect=RuntimeError("Unexpected crash")):
            response = self._book(self.student_client, self.valid_start, lesson_option='trial')
        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertIn('error', data)
        self.assertIn('unexpected error occurred', data['error'].lower())
