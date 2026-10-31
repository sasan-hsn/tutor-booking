import json
import uuid
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from chat.models import Conversation, Message
from chat.services import send_message
from chat.utils import CHAT_RATE_LIMIT, record_chat_message

User = get_user_model()


class ChatViewsIntegrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            username="student_user",
            email="student@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.teacher = User.objects.create_user(
            username="teacher_user",
            email="teacher@example.com",
            password="testpassword123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.unverified_student = User.objects.create_user(
            username="unverified_student",
            email="unverified@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=False,
        )
        self.other_teacher = User.objects.create_user(
            username="other_teacher",
            email="other_teacher@example.com",
            password="testpassword123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.other_student = User.objects.create_user(
            username="other_student",
            email="other_student@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

        self.client = Client()

    def tearDown(self):
        cache.clear()

    # --- Test /messages/with/<teacher_username>/ ---

    def test_start_conversation_redirects_unauthenticated(self):
        url = reverse("chat:start_conversation", kwargs={"teacher_username": self.teacher.username})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_start_conversation_blocked_for_unverified_student(self):
        self.client.force_login(self.unverified_student)
        url = reverse("chat:start_conversation", kwargs={"teacher_username": self.teacher.username})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Please verify your email", status_code=403)

    def test_start_conversation_validates_teacher_role(self):
        self.client.force_login(self.student)
        # Attempting with another student should 404 or 400
        url = reverse("chat:start_conversation", kwargs={"teacher_username": self.other_student.username})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

        # Non-existent user should 404
        url_nonexistent = reverse("chat:start_conversation", kwargs={"teacher_username": "nobody"})
        response = self.client.get(url_nonexistent)
        self.assertEqual(response.status_code, 404)

    def test_start_conversation_as_teacher_blocked(self):
        self.client.force_login(self.teacher)
        url = reverse("chat:start_conversation", kwargs={"teacher_username": self.other_teacher.username})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_start_conversation_creates_and_redirects_idempotently(self):
        self.client.force_login(self.student)
        url = reverse("chat:start_conversation", kwargs={"teacher_username": self.teacher.username})

        # First visit: creates conversation and redirects
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        conv = Conversation.objects.get(student=self.student, teacher=self.teacher)
        expected_url = reverse("chat:conversation_detail", kwargs={"conversation_id": conv.id})
        self.assertRedirects(response, expected_url)

        # Second visit: idempotently redirects to same conversation
        response2 = self.client.get(url)
        self.assertEqual(response2.status_code, 302)
        self.assertRedirects(response2, expected_url)
        self.assertEqual(Conversation.objects.filter(student=self.student, teacher=self.teacher).count(), 1)

    # --- Test /messages/<conversation_id>/ ---

    def test_conversation_detail_access_control(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        url = reverse("chat:conversation_detail", kwargs={"conversation_id": conv.id})

        # Anonymous -> login redirect
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        # Outsider user -> 404
        self.client.force_login(self.other_student)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

        # Authorized student -> 200
        self.client.force_login(self.student)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Authorized teacher -> 200
        self.client.force_login(self.teacher)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_conversation_detail_advances_watermark_for_viewer(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        msg1 = send_message(conversation=conv, sender=self.student, text="Hello!")
        msg2 = send_message(conversation=conv, sender=self.student, text="Are you available?")

        self.client.force_login(self.teacher)
        url = reverse("chat:conversation_detail", kwargs={"conversation_id": conv.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        conv.refresh_from_db()
        self.assertEqual(conv.teacher_last_read_message_id, msg2.id)

    def test_conversation_detail_renders_linkified_text(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        msg = send_message(
            conversation=conv,
            sender=self.student,
            text="Check our resource: https://example.com/grammar",
        )

        self.client.force_login(self.teacher)
        url = reverse("chat:conversation_detail", kwargs={"conversation_id": conv.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="https://example.com/grammar"')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, 'rel="noopener noreferrer"')

    # --- Test /messages/<conversation_id>/send/ ---

    def test_send_message_unauthenticated(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        url = reverse("chat:send_message", kwargs={"conversation_id": conv.id})
        response = self.client.post(url, {"text": "Test"})
        self.assertEqual(response.status_code, 302)

    def test_send_message_unverified_student_blocked(self):
        conv = Conversation.objects.create(student=self.unverified_student, teacher=self.teacher)
        self.client.force_login(self.unverified_student)
        url = reverse("chat:send_message", kwargs={"conversation_id": conv.id})
        response = self.client.post(url, {"text": "Test message"})
        self.assertEqual(response.status_code, 403)
        data = json.loads(response.content)
        self.assertEqual(data.get("error"), "email_unverified")

    def test_send_message_outsider_blocked(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        self.client.force_login(self.other_student)
        url = reverse("chat:send_message", kwargs={"conversation_id": conv.id})
        response = self.client.post(url, {"text": "Intrusion"})
        self.assertEqual(response.status_code, 404)

    def test_send_message_empty_text_rejected(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        self.client.force_login(self.student)
        url = reverse("chat:send_message", kwargs={"conversation_id": conv.id})
        response = self.client.post(url, {"text": "   "})
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn("error", data)

    def test_send_message_over_2000_chars_rejected(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        self.client.force_login(self.student)
        url = reverse("chat:send_message", kwargs={"conversation_id": conv.id})
        response = self.client.post(url, {"text": "a" * 2001})
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn("error", data)

    def test_send_message_success_json_and_db_state(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        self.client.force_login(self.student)
        url = reverse("chat:send_message", kwargs={"conversation_id": conv.id})
        response = self.client.post(url, {"text": "Hello teacher, look at https://example.com!"})

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data.get("success"))
        self.assertIn("message", data)
        msg_data = data["message"]
        self.assertEqual(msg_data["sender_id"], self.student.id)
        self.assertIn("href=\"https://example.com\"", msg_data["rendered_text"])

        conv.refresh_from_db()
        self.assertEqual(conv.student_last_read_message_id, msg_data["id"])
        self.assertEqual(conv.last_message_snippet, "Hello teacher, look at https://example.com!")

    def test_send_message_rate_limiting(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        self.client.force_login(self.student)
        url = reverse("chat:send_message", kwargs={"conversation_id": conv.id})

        # Pre-seed rate limit cache to CHAT_RATE_LIMIT
        for _ in range(CHAT_RATE_LIMIT):
            record_chat_message(user_id=self.student.id, ip="127.0.0.1")

        response = self.client.post(url, {"text": "Blocked by rate limit"})
        self.assertEqual(response.status_code, 429)
        data = json.loads(response.content)
        self.assertEqual(data.get("error"), "rate_limited")
        self.assertIn("retry_after", data)
