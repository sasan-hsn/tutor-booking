from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from booking.models import Booking

User = get_user_model()


class LessonMessageShortcutsTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.lesson_price = Decimal("25.00")
        cls.teacher.save()

        now = timezone.now()
        # Upcoming confirmed booking
        cls.upcoming_booking = Booking.objects.create(
            student=cls.student_user,
            teacher=cls.teacher,
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, minutes=50),
            lesson_type=Booking.LessonType.REGULAR,
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
            price=Decimal("25.00"),
        )

        # Past completed booking
        cls.past_completed_booking = Booking.objects.create(
            student=cls.student_user,
            teacher=cls.teacher,
            start_at=now - timedelta(days=2),
            end_at=now - timedelta(days=2) + timedelta(minutes=50),
            lesson_type=Booking.LessonType.REGULAR,
            duration_minutes=50,
            status=Booking.Status.COMPLETED,
            price=Decimal("25.00"),
        )

        # Past cancelled booking
        cls.cancelled_booking = Booking.objects.create(
            student=cls.student_user,
            teacher=cls.teacher,
            start_at=now - timedelta(days=4),
            end_at=now - timedelta(days=4) + timedelta(minutes=50),
            lesson_type=Booking.LessonType.REGULAR,
            duration_minutes=50,
            status=Booking.Status.CANCELLED,
            price=Decimal("25.00"),
        )

    def test_upcoming_lesson_card_renders_message_shortcut_for_teacher(self):
        """Teacher viewing upcoming lesson cards sees message shortcut targeting the student."""
        response = self.teacher_client.get(
            reverse("booking:teacher_dashboard"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            data={"section": "upcoming", "page": 1},
        )
        self.assertEqual(response.status_code, 200)
        expected_url = reverse(
            "chat:start_conversation",
            kwargs={"username": self.student_user.username},
        )
        self.assertContains(response, expected_url)
        self.assertContains(response, "btn-message-counterpart")
        self.assertContains(response, "bi-chat-dots")
        self.assertContains(response, "Message")

    def test_upcoming_lesson_card_renders_message_shortcut_for_student(self):
        """Student viewing upcoming lesson cards sees message shortcut targeting the teacher."""
        response = self.student_client.get(
            reverse("booking:student_dashboard"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            data={"section": "upcoming", "page": 1},
        )
        self.assertEqual(response.status_code, 200)
        expected_url = reverse(
            "chat:start_conversation",
            kwargs={"username": self.teacher_user.username},
        )
        self.assertContains(response, expected_url)
        self.assertContains(response, "btn-message-counterpart")
        self.assertContains(response, "bi-chat-dots")
        self.assertContains(response, "Message")

    def test_past_lesson_card_renders_message_shortcut_for_teacher(self):
        """Teacher viewing past lesson cards sees message shortcut targeting the student."""
        response = self.teacher_client.get(
            reverse("booking:teacher_dashboard"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            data={"section": "past", "page": 1},
        )
        self.assertEqual(response.status_code, 200)
        expected_url = reverse(
            "chat:start_conversation",
            kwargs={"username": self.student_user.username},
        )
        self.assertContains(response, expected_url)
        self.assertContains(response, "btn-message-counterpart")
        self.assertContains(response, "bi-chat-dots")
        self.assertContains(response, "Message")

    def test_past_lesson_card_renders_message_shortcut_for_student(self):
        """Student viewing past lesson cards sees message shortcut targeting the teacher."""
        response = self.student_client.get(
            reverse("booking:student_dashboard"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            data={"section": "past", "page": 1},
        )
        self.assertEqual(response.status_code, 200)
        expected_url = reverse(
            "chat:start_conversation",
            kwargs={"username": self.teacher_user.username},
        )
        self.assertContains(response, expected_url)
        self.assertContains(response, "btn-message-counterpart")
        self.assertContains(response, "bi-chat-dots")
        self.assertContains(response, "Message")

    def test_teacher_lesson_detail_modal_renders_message_shortcut_across_states(self):
        """Teacher viewing lesson detail modal sees message button for confirmed, completed, and cancelled bookings."""
        expected_url = reverse(
            "chat:start_conversation",
            kwargs={"username": self.student_user.username},
        )

        for booking in [
            self.upcoming_booking,
            self.past_completed_booking,
            self.cancelled_booking,
        ]:
            with self.subTest(status=booking.status):
                response = self.teacher_client.get(
                    reverse("booking:lesson_detail", kwargs={"booking_id": booking.id})
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected_url)
                self.assertContains(response, "btn-message-counterpart")
                self.assertContains(response, "bi-chat-dots")
                self.assertContains(response, "Message")

    def test_student_lesson_detail_modal_renders_message_shortcut_across_states(self):
        """Student viewing lesson detail modal sees message button for confirmed, completed, and cancelled bookings."""
        expected_url = reverse(
            "chat:start_conversation",
            kwargs={"username": self.teacher_user.username},
        )

        for booking in [
            self.upcoming_booking,
            self.past_completed_booking,
            self.cancelled_booking,
        ]:
            with self.subTest(status=booking.status):
                response = self.student_client.get(
                    reverse(
                        "booking:lesson_detail_student",
                        kwargs={"booking_id": booking.id},
                    )
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected_url)
                self.assertContains(response, "btn-message-counterpart")
                self.assertContains(response, "bi-chat-dots")
                self.assertContains(response, "Message")

    def test_null_safe_counterpart_rendering_for_teacher(self):
        """
        When rendering cards or modal from teacher perspective with a booking where student is None/unbooked,
        it should not raise NoReverseMatch or crash, and simply omit the message action button.
        """
        factory = RequestFactory()
        request = factory.get("/")
        request.user = self.teacher_user

        class DummyBooking:
            id = 999
            student = None
            teacher = self.teacher
            student_display_name = "Unbooked"
            teacher_display_name = self.teacher_user.username
            student_display_user = self.student_user
            teacher_display_user = self.teacher_user
            teacher_local_start = timezone.now()
            teacher_local_end = timezone.now() + timedelta(minutes=50)
            student_local_start = timezone.now()
            student_local_end = timezone.now() + timedelta(minutes=50)
            duration_display = "50 min"
            status = "pending"
            get_status_display = lambda self: "Pending"  # noqa: E731
            get_lesson_type_display = lambda self: "Regular"  # noqa: E731
            is_awaiting_resolution = False
            is_joinable = False
            is_live = False
            meeting_link = ""
            review = None
            price = Decimal("25.00")
            completion_note = ""

        dummy = DummyBooking()

        # Render upcoming card items
        rendered_upcoming = render_to_string(
            "partials/_lesson_card_items.html",
            {"upcoming_bookings": [dummy], "user": self.teacher_user},
            request=request,
        )
        self.assertNotIn("chat:start_conversation", rendered_upcoming)
        self.assertNotIn("btn-message-counterpart", rendered_upcoming)

        # Render past card items
        rendered_past = render_to_string(
            "partials/_past_lesson_card_items.html",
            {"past_bookings": [dummy], "user": self.teacher_user},
            request=request,
        )
        self.assertNotIn("chat:start_conversation", rendered_past)
        self.assertNotIn("btn-message-counterpart", rendered_past)

        # Render modal
        rendered_modal = render_to_string(
            "partials/_lesson_detail_modal.html",
            {"booking": dummy, "user": self.teacher_user, "date_time_label": "Today"},
            request=request,
        )
        self.assertNotIn("chat:start_conversation", rendered_modal)
        self.assertNotIn("btn-message-counterpart", rendered_modal)
