from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from booking.models import Booking
from chat.models import Conversation
from chat.services import get_or_create_conversation, send_message

User = get_user_model()


class ProspectiveStudentInquiryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

        self.student = User.objects.create_user(
            username="prospective_student",
            email="prospective@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.teacher_user = User.objects.create_user(
            username="teacher_jane",
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
            password="testpassword123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher_profile = self.teacher_user.teacher_profile

    def tearDown(self):
        cache.clear()

    # --- Seam 1: Messages Entry / Direct Single-Pane Seam ---

    def test_student_without_conversation_renders_empty_timeline_with_inquiry_guidance(
        self,
    ):
        """
        A prospective student without an existing conversation visiting /messages/
        is directly routed to their single-pane conversation view with #274 inviting guidance.
        """
        self.client.force_login(self.student)
        response = self.client.get(reverse("chat:inbox"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chat/conversation_detail.html")
        self.assertTrue(response.context.get("is_prospective_student"))

        # Renders the prompt inviting conversation before booking inside the timeline
        self.assertContains(response, 'id="chatEmptyState"')
        self.assertContains(response, "Have questions before booking?")
        self.assertContains(response, "Send your first message below")

    # --- Seam 2: Initiation Redirect Seam ---

    def test_student_initiation_resolves_or_creates_and_redirects(self):
        """
        GET /messages/with/<teacher_username>/ creates or resolves the conversation
        and redirects to the conversation timeline.
        """
        self.client.force_login(self.student)
        url = reverse(
            "chat:start_conversation",
            kwargs={"username": self.teacher_user.username},
        )

        # First visit: creates the conversation and redirects
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        conversation = Conversation.objects.get(
            student=self.student,
            teacher=self.teacher_user,
        )
        expected_redirect = reverse(
            "chat:conversation_detail",
            kwargs={"conversation_id": conversation.id},
        )
        self.assertRedirects(response, expected_redirect)

        # Second visit: idempotent resolution without duplication
        second_response = self.client.get(url)
        self.assertRedirects(second_response, expected_redirect)
        self.assertEqual(
            Conversation.objects.filter(
                student=self.student,
                teacher=self.teacher_user,
            ).count(),
            1,
        )

    # --- Seam 3: Empty Conversation Timeline Seam ---

    def test_empty_conversation_timeline_renders_inviting_guidance_for_student(self):
        """
        GET /messages/<conversation_id>/ with zero messages displays an inviting empty state
        providing clear guidance to send their first question before booking.
        """
        conversation, _ = get_or_create_conversation(
            student=self.student,
            teacher=self.teacher_user,
        )
        self.client.force_login(self.student)
        url = reverse(
            "chat:conversation_detail",
            kwargs={"conversation_id": conversation.id},
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chat/conversation_detail.html")
        self.assertContains(response, 'id="chatEmptyState"')
        self.assertContains(response, "Have questions before booking?")
        self.assertContains(response, "Send your first message below")

    def test_conversation_timeline_with_messages_hides_empty_state(self):
        """
        Once messages exist in the conversation timeline, the empty state is not rendered.
        """
        conversation, _ = get_or_create_conversation(
            student=self.student,
            teacher=self.teacher_user,
        )
        send_message(
            conversation=conversation,
            sender=self.student,
            text="Hello teacher, I have a question!",
        )

        self.client.force_login(self.student)
        url = reverse(
            "chat:conversation_detail",
            kwargs={"conversation_id": conversation.id},
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="chatEmptyState"')
        self.assertContains(response, "Hello teacher, I have a question!")

    def test_teacher_views_empty_conversation_timeline(self):
        """
        When a teacher views a conversation with zero messages, they see the generic greeting prompt.
        """
        conversation, _ = get_or_create_conversation(
            student=self.student,
            teacher=self.teacher_user,
        )
        self.client.force_login(self.teacher_user)
        url = reverse(
            "chat:conversation_detail",
            kwargs={"conversation_id": conversation.id},
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="chatEmptyState"')
        self.assertContains(response, "Say hello to start the conversation!")

    def test_student_inbox_with_active_conversation_renders_direct_timeline(
        self,
    ):
        """
        When a conversation has messages, the student visiting /messages/ directly
        receives the single-pane conversation timeline with the message.
        """
        conversation, _ = get_or_create_conversation(
            student=self.student,
            teacher=self.teacher_user,
        )
        send_message(
            conversation=conversation,
            sender=self.student,
            text="Hi Jane, what is your schedule?",
        )

        self.client.force_login(self.student)
        response = self.client.get(reverse("chat:inbox"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chat/conversation_detail.html")
        self.assertContains(response, "Hi Jane, what is your schedule?")

    def test_enrolled_student_sees_enrolled_empty_state_copy(self):
        """
        An enrolled student with an existing booking sees the general conversation prompt
        rather than prospective 'before booking' inquiry copy.
        """
        now = timezone.now()
        Booking.objects.create(
            student=self.student,
            teacher=self.teacher_profile,
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, minutes=50),
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
        )
        conversation, _ = get_or_create_conversation(
            student=self.student,
            teacher=self.teacher_user,
        )
        self.client.force_login(self.student)
        url = reverse(
            "chat:conversation_detail",
            kwargs={"conversation_id": conversation.id},
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="chatEmptyState"')
        self.assertContains(response, "Start the conversation")
        self.assertNotContains(response, "Have questions before booking?")
