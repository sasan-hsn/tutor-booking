from datetime import time, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo
from django.urls import reverse
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from booking.models import Booking, RegularAvailability
from chat.models import Conversation, Message


class BookingChatIntegrationTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.lesson_price = Decimal("20.00")
        cls.teacher.lesson_price_25 = Decimal("12.00")
        cls.teacher.lesson_duration_minutes = 50
        cls.teacher.offers_trial = True
        cls.teacher.trial_price = Decimal("5.00")
        cls.teacher.trial_duration_minutes = 25
        cls.teacher.save()

    def setUp(self):
        super().setUp()
        self.tz = ZoneInfo(self.teacher_user.timezone)
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

    def _make_booking(self, status, hours_from_now=5, duration_minutes=50):
        start = timezone.now() + timedelta(hours=hours_from_now)
        return Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start,
            end_at=start + timedelta(minutes=duration_minutes),
            duration_minutes=duration_minutes,
            lesson_type=Booking.LessonType.REGULAR,
            status=status,
            price=Decimal("20.00"),
        )

    def test_book_slot_records_booking_requested_system_event(self):
        data = {
            "start_at": self.valid_start.isoformat(),
            "lesson_option": "trial",
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.student_client.post(reverse("booking:book_slot"), data)

        self.assertEqual(response.status_code, 200)
        booking = Booking.objects.get(student=self.student_user)

        conv = Conversation.objects.filter(
            student=self.student_user, teacher=self.teacher_user
        ).first()
        self.assertIsNotNone(conv)
        self.assertTrue(conv.last_message_snippet.startswith("📅 Lesson Request submitted for"))

        msg = conv.messages.filter(message_type=Message.MessageType.SYSTEM_EVENT).first()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.sender, self.student_user)
        self.assertEqual(msg.metadata["event_type"], "booking_requested")
        self.assertEqual(msg.metadata["booking_id"], booking.id)

    def test_respond_to_booking_accept_records_booking_confirmed_event(self):
        booking = self._make_booking(Booking.Status.PENDING)

        url = reverse("booking:respond_to_booking", kwargs={"booking_id": booking.id})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(url, {"action": "accept"})

        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

        conv = Conversation.objects.get(student=self.student_user, teacher=self.teacher_user)
        self.assertTrue(conv.last_message_snippet.startswith("✅ Lesson Confirmed for"))

        msg = conv.messages.filter(message_type=Message.MessageType.SYSTEM_EVENT).last()
        self.assertEqual(msg.sender, self.teacher_user)
        self.assertEqual(msg.metadata["event_type"], "booking_confirmed")
        self.assertEqual(msg.metadata["booking_id"], booking.id)

    def test_respond_to_booking_decline_records_booking_cancelled_event(self):
        booking = self._make_booking(Booking.Status.PENDING)

        url = reverse("booking:respond_to_booking", kwargs={"booking_id": booking.id})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(url, {"action": "decline"})

        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)

        conv = Conversation.objects.get(student=self.student_user, teacher=self.teacher_user)
        self.assertTrue(conv.last_message_snippet.startswith("❌ Lesson Cancelled for"))

        msg = conv.messages.filter(message_type=Message.MessageType.SYSTEM_EVENT).last()
        self.assertEqual(msg.sender, self.teacher_user)
        self.assertEqual(msg.metadata["event_type"], "booking_cancelled")
        self.assertEqual(msg.metadata["booking_id"], booking.id)

    def test_respond_to_cancellation_request_accept_records_booking_cancelled_event(self):
        booking = self._make_booking(Booking.Status.CONFIRMED)
        booking.cancellation_requested = True
        booking.save()

        url = reverse("booking:respond_to_booking", kwargs={"booking_id": booking.id})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(url, {"action": "accept"})

        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)

        conv = Conversation.objects.get(student=self.student_user, teacher=self.teacher_user)
        self.assertTrue(conv.last_message_snippet.startswith("❌ Lesson Cancelled for"))

        msg = conv.messages.filter(message_type=Message.MessageType.SYSTEM_EVENT).last()
        self.assertEqual(msg.metadata["event_type"], "booking_cancelled")

    def test_cancel_lesson_records_booking_cancelled_event(self):
        booking = self._make_booking(Booking.Status.CONFIRMED)

        url = reverse("booking:cancel_lesson", kwargs={"booking_id": booking.id})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(url)

        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)

        conv = Conversation.objects.get(student=self.student_user, teacher=self.teacher_user)
        self.assertTrue(conv.last_message_snippet.startswith("❌ Lesson Cancelled for"))

        msg = conv.messages.filter(message_type=Message.MessageType.SYSTEM_EVENT).last()
        self.assertEqual(msg.sender, self.teacher_user)
        self.assertEqual(msg.metadata["event_type"], "booking_cancelled")

    def test_request_cancellation_records_cancellation_requested_event(self):
        booking = self._make_booking(Booking.Status.CONFIRMED)

        url = reverse("booking:request_cancellation", kwargs={"booking_id": booking.id})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.student_client.post(url)

        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertTrue(booking.cancellation_requested)

        conv = Conversation.objects.get(student=self.student_user, teacher=self.teacher_user)
        self.assertTrue(conv.last_message_snippet.startswith("⚠️ Cancellation Requested for"))

        msg = conv.messages.filter(message_type=Message.MessageType.SYSTEM_EVENT).last()
        self.assertEqual(msg.sender, self.student_user)
        self.assertEqual(msg.metadata["event_type"], "cancellation_requested")

    def test_booking_actions_succeed_even_if_chat_service_fails(self):
        # Verify resilience: even if record_booking_event raises an unexpected error, booking flow is not broken
        data = {
            "start_at": self.valid_start.isoformat(),
            "lesson_option": "trial",
        }
        with patch("booking.views.record_booking_event", side_effect=Exception("Redis/DB down")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.student_client.post(reverse("booking:book_slot"), data)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Booking.objects.filter(student=self.student_user).exists())
