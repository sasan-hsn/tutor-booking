from datetime import timedelta, time, date
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
        cls.teacher.lesson_price = 20
        cls.teacher.lesson_duration_minutes = 60
        cls.teacher.offers_trial = True
        cls.teacher.trial_price = 5
        cls.teacher.trial_duration_minutes = 30
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

    def _book(self, client, start_at):
        return client.post(
            reverse('booking:book_slot'),
            {'start_at': start_at.isoformat()},
        )

    def test_successful_booking_creates_confirmed_booking(self):
        response = self._book(self.student_client, self.valid_start)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Booking.objects.count(), 1)
        booking = Booking.objects.first()
        self.assertEqual(booking.student, self.student_user)
        self.assertEqual(booking.teacher, self.teacher)
        self.assertEqual(booking.start_at, self.valid_start)

    def test_first_time_student_gets_trial_pricing(self):
        self._book(self.student_client, self.valid_start)
        booking = Booking.objects.first()
        self.assertEqual(booking.lesson_type, Booking.LessonType.TRIAL)
        self.assertEqual(booking.price, self.teacher.trial_price)

    def test_returning_student_gets_regular_pricing(self):
        # simulate a previous completed lesson
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=timezone.now() - timedelta(days=10),
            end_at=timezone.now() - timedelta(days=10) + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )
        self._book(self.student_client, self.valid_start)
        booking = Booking.objects.get(start_at=self.valid_start)
        self.assertEqual(booking.lesson_type, Booking.LessonType.REGULAR)
        self.assertEqual(booking.price, self.teacher.lesson_price)

    def test_missing_start_at_returns_400(self):
        response = self.student_client.post(reverse('booking:book_slot'), {})
        self.assertEqual(response.status_code, 400)

    def test_invalid_start_at_format_returns_400(self):
        response = self.student_client.post(
            reverse('booking:book_slot'), {'start_at': 'not-a-date'}
        )
        self.assertEqual(response.status_code, 400)

    def test_naive_start_at_returns_400(self):
        naive = timezone.datetime.combine(self.future_day, time(10, 0))
        response = self.student_client.post(
            reverse('booking:book_slot'), {'start_at': naive.isoformat()}
        )
        self.assertEqual(response.status_code, 400)

    def test_unavailable_slot_returns_409(self):
        past_start = timezone.now() - timedelta(days=1)
        response = self._book(self.student_client, past_start)
        self.assertEqual(response.status_code, 409)

    def test_already_booked_slot_returns_409(self):
        self._book(self.student_client, self.valid_start)

        from accounts.models import User
        another_student = User.objects.create_user(
            username='second_student', password='password123', role=User.Role.STUDENT
        )
        another_client = self.client_class()
        another_client.force_login(another_student)

        response = self._book(another_client, self.valid_start)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Booking.objects.count(), 1)

    def test_teacher_cannot_book_own_slot(self):
        # log the teacher's own user in as if they were trying to book
        response = self.teacher_client.post(
            reverse('booking:book_slot'), {'start_at': self.valid_start.isoformat()}
        )
        # note: book_slot is @student_required, so a teacher hitting
        # this URL should get 403 from the decorator before reaching
        # the self-booking check at all — this test actually verifies
        # access control, not the self-booking business rule
        self.assertEqual(response.status_code, 403)