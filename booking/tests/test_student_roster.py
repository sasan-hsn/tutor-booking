from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from booking.models import Booking

User = get_user_model()


class TeacherStudentManagementRosterTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.lesson_price = Decimal("20.00")
        cls.teacher.save()

    def test_student_roster_renders_message_student_action_shortcut(self):
        """
        Each student row in teacher_student_management.html must display
        a 'Message Student' action button linking directly to /messages/with/<student_username>/.
        """
        now = timezone.now()
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, minutes=50),
            lesson_type=Booking.LessonType.REGULAR,
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
            price=Decimal("20.00"),
        )

        response = self.teacher_client.get(reverse("booking:student_management"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "booking/teacher_student_management.html")

        expected_url = reverse(
            "chat:start_conversation",
            kwargs={"username": self.student_user.username},
        )
        self.assertContains(response, expected_url)
        self.assertContains(response, "Message Student")
        self.assertContains(response, "bi-chat-dots")

    def test_student_roster_empty_state_has_no_message_button(self):
        """When teacher has no enrolled students, empty state is shown without action button."""
        response = self.teacher_client.get(reverse("booking:student_management"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You don't have any students yet.")
        self.assertNotContains(response, "Message Student")
