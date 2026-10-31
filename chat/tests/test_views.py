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

    def test_conversation_detail_renders_read_receipts_and_typing_indicator(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        msg1 = send_message(conversation=conv, sender=self.student, text="Read message")
        msg2 = send_message(conversation=conv, sender=self.student, text="Unread message")

        # Teacher has read msg1 but not msg2
        conv.teacher_last_read_message_id = msg1.id
        conv.save()

        # Student views conversation
        self.client.force_login(self.student)
        url = reverse("chat:conversation_detail", kwargs={"conversation_id": conv.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["counterpart_watermark"], msg1.id)
        self.assertContains(response, 'id="chatTypingIndicator"')
        self.assertContains(response, 'data-read="true"')
        self.assertContains(response, 'data-read="false"')
        self.assertFalse(response.context["has_more_history"])

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

    # --- Test /messages/ (Inbox) ---

    def test_inbox_redirects_unauthenticated(self):
        url = reverse("chat:inbox")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_inbox_lists_active_conversations_ordered_by_last_message_at_desc(self):
        # Student has conversations with teacher and other_teacher
        conv1 = Conversation.objects.create(student=self.student, teacher=self.teacher)
        send_message(conv1, sender=self.student, text="First conversation message")

        conv2 = Conversation.objects.create(student=self.student, teacher=self.other_teacher)
        send_message(conv2, sender=self.other_teacher, text="Second conversation message")

        # Third conversation that is uninitiated
        third_teacher = User.objects.create_user(
            username="third_teacher",
            email="third_teacher@example.com",
            password="testpassword123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        conv3 = Conversation.objects.create(student=self.student, teacher=third_teacher)

        self.client.force_login(self.student)
        url = reverse("chat:inbox")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        conversations = response.context["conversations"]
        # conv2 should be first, then conv1. conv3 should be omitted.
        self.assertEqual(len(conversations), 2)
        self.assertEqual(conversations[0]["conversation"].id, conv2.id)
        self.assertEqual(conversations[1]["conversation"].id, conv1.id)

    def test_inbox_omits_uninitiated_conversations(self):
        # Conversation with no messages (last_message_at is None)
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        self.assertIsNone(conv.last_message_at)

        self.client.force_login(self.student)
        url = reverse("chat:inbox")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["conversations"]), 0)

    def test_inbox_displays_counterpart_info_and_unread_count(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        msg1 = send_message(conv, sender=self.teacher, text="Message 1 from teacher")
        msg2 = send_message(conv, sender=self.teacher, text="Message 2 from teacher")

        # Student has not read any message
        self.client.force_login(self.student)
        url = reverse("chat:inbox")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        item = response.context["conversations"][0]
        self.assertEqual(item["counterpart"], self.teacher)
        self.assertEqual(item["unread_count"], 2)
        self.assertEqual(item["conversation"].last_message_snippet, "Message 2 from teacher")
        self.assertContains(response, self.teacher.username)
        self.assertContains(response, "Message 2 from teacher")

        # Now student marks msg1 as read
        Conversation.objects.filter(id=conv.id).update(student_last_read_message_id=msg1.id)
        response = self.client.get(url)
        item = response.context["conversations"][0]
        self.assertEqual(item["unread_count"], 1)

    def test_inbox_accessible_by_teacher(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        send_message(conv, sender=self.student, text="Hello teacher!")

        self.client.force_login(self.teacher)
        url = reverse("chat:inbox")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        item = response.context["conversations"][0]
        self.assertEqual(item["counterpart"], self.student)
        self.assertEqual(item["unread_count"], 1)

    # --- Test /messages/<conversation_id>/history/ ---

    def test_conversation_history_access_control(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        send_message(conv, sender=self.student, text="Hello")
        url = reverse("chat:conversation_history", kwargs={"conversation_id": conv.id})

        # Anonymous -> 302
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        # Outsider user -> 404
        self.client.force_login(self.other_student)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

        # Non-GET -> 405
        self.client.force_login(self.student)
        post_response = self.client.post(url)
        self.assertEqual(post_response.status_code, 405)

    def test_conversation_history_reverse_pagination(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        messages = []
        for i in range(45):
            msg = send_message(conv, sender=self.student, text=f"Message {i + 1}")
            messages.append(msg)

        # Initial page view only loads the latest 30 messages (Message 16 to 45)
        self.client.force_login(self.student)
        detail_url = reverse("chat:conversation_detail", kwargs={"conversation_id": conv.id})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["messages"]), 30)
        self.assertTrue(response.context["has_more_history"])
        oldest_initial = response.context["messages"][0]
        self.assertEqual(oldest_initial.text, "Message 16")

        # Now paginate before oldest_initial.id
        history_url = reverse("chat:conversation_history", kwargs={"conversation_id": conv.id})
        history_response = self.client.get(f"{history_url}?before={oldest_initial.id}")
        self.assertEqual(history_response.status_code, 200)
        data = json.loads(history_response.content)

        # Should return 15 remaining messages (Message 1 to 15) in chronological order
        self.assertEqual(len(data["messages"]), 15)
        self.assertEqual(data["messages"][0]["text"], "Message 1")
        self.assertEqual(data["messages"][-1]["text"], "Message 15")
        self.assertFalse(data["has_more"])

    def test_conversation_history_multi_page_pagination(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        messages = []
        for i in range(70):
            msg = send_message(conv, sender=self.student, text=f"Message {i + 1}")
            messages.append(msg)

        self.client.force_login(self.student)
        history_url = reverse("chat:conversation_history", kwargs={"conversation_id": conv.id})

        # Request before msg 70 (latest)
        latest_id = messages[-1].id
        res1 = self.client.get(f"{history_url}?before={latest_id}")
        self.assertEqual(res1.status_code, 200)
        data1 = json.loads(res1.content)
        self.assertEqual(len(data1["messages"]), 30)
        self.assertTrue(data1["has_more"])
        # Should be messages 40 to 69 (30 messages)
        self.assertEqual(data1["messages"][-1]["id"], messages[-2].id)

        # Request before oldest of page 1
        oldest_id_p1 = data1["messages"][0]["id"]
        res2 = self.client.get(f"{history_url}?before={oldest_id_p1}")
        data2 = json.loads(res2.content)
        self.assertEqual(len(data2["messages"]), 30)
        self.assertTrue(data2["has_more"])

        # Request before oldest of page 2
        oldest_id_p2 = data2["messages"][0]["id"]
        res3 = self.client.get(f"{history_url}?before={oldest_id_p2}")
        data3 = json.loads(res3.content)
        self.assertEqual(len(data3["messages"]), 9)  # 70 - 1 - 30 - 30 = 9
        self.assertFalse(data3["has_more"])

    def test_conversation_history_invalid_before_parameter(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        send_message(conv, sender=self.student, text="Hello")

        self.client.force_login(self.student)
        history_url = reverse("chat:conversation_history", kwargs={"conversation_id": conv.id})

        # Non-integer before
        res = self.client.get(f"{history_url}?before=abc")
        self.assertEqual(res.status_code, 400)

        # Negative before
        res2 = self.client.get(f"{history_url}?before=-5")
        self.assertEqual(res2.status_code, 400)


