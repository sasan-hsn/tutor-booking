from unittest.mock import AsyncMock, patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from chat.models import Conversation, Message
from chat.services import (
    advance_watermark,
    broadcast_unread_chat_count,
    get_or_create_conversation,
    get_total_unread_chat_count,
    send_message,
)

User = get_user_model()


class ChatServicesTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="student_user",
            email="student@example.com",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.teacher = User.objects.create_user(
            username="teacher_user",
            email="teacher@example.com",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )

    def test_get_or_create_conversation_creates_new(self):
        conv, created = get_or_create_conversation(student=self.student, teacher=self.teacher)
        self.assertTrue(created)
        self.assertEqual(conv.student, self.student)
        self.assertEqual(conv.teacher, self.teacher)

    def test_get_or_create_conversation_idempotent(self):
        conv1, created1 = get_or_create_conversation(student=self.student, teacher=self.teacher)
        conv2, created2 = get_or_create_conversation(student=self.student, teacher=self.teacher)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(conv1.id, conv2.id)

    def test_get_or_create_conversation_validates_roles(self):
        with self.assertRaises(ValidationError):
            get_or_create_conversation(student=self.teacher, teacher=self.student)

    def test_send_message_atomic_update(self):
        conv, _ = get_or_create_conversation(student=self.student, teacher=self.teacher)

        message = send_message(
            conversation=conv,
            sender=self.student,
            text="Hello! This is an exciting inquiry.",
        )

        self.assertIsInstance(message, Message)
        self.assertEqual(message.text, "Hello! This is an exciting inquiry.")
        self.assertEqual(message.conversation, conv)
        self.assertEqual(message.sender, self.student)

        # Refresh conversation from DB
        conv.refresh_from_db()
        self.assertEqual(conv.student_last_read_message_id, message.id)
        self.assertEqual(conv.teacher_last_read_message_id, 0)
        self.assertEqual(conv.last_message_at, message.created_at)
        self.assertEqual(conv.last_message_snippet, "Hello! This is an exciting inquiry.")

    def test_send_message_truncates_snippet_to_120_chars(self):
        conv, _ = get_or_create_conversation(student=self.student, teacher=self.teacher)
        long_text = "x" * 200

        message = send_message(
            conversation=conv,
            sender=self.student,
            text=long_text,
        )

        conv.refresh_from_db()
        self.assertEqual(len(conv.last_message_snippet), 120)
        self.assertTrue(conv.last_message_snippet.endswith("..."))

    def test_advance_watermark_is_monotonic(self):
        conv, _ = get_or_create_conversation(student=self.student, teacher=self.teacher)
        msg1 = send_message(conversation=conv, sender=self.student, text="Msg 1")
        msg2 = send_message(conversation=conv, sender=self.student, text="Msg 2")

        advance_watermark(conversation=conv, user=self.teacher, message_id=msg2.id)
        conv.refresh_from_db()
        self.assertEqual(conv.teacher_last_read_message_id, msg2.id)

        # Attempt to regress watermark to msg1 should have no effect
        advance_watermark(conversation=conv, user=self.teacher, message_id=msg1.id)
        conv.refresh_from_db()
        self.assertEqual(conv.teacher_last_read_message_id, msg2.id)

    def test_get_total_unread_chat_count(self):
        other_student = User.objects.create_user(
            username="other_student",
            email="other_student@example.com",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        conv1, _ = get_or_create_conversation(self.student, self.teacher)
        conv2, _ = get_or_create_conversation(other_student, self.teacher)

        # Initially 0
        self.assertEqual(get_total_unread_chat_count(self.teacher), 0)
        self.assertEqual(get_total_unread_chat_count(self.student), 0)
        self.assertEqual(get_total_unread_chat_count(None), 0)

        # Student 1 sends 2 messages
        msg1 = send_message(conv1, sender=self.student, text="Hello 1")
        msg2 = send_message(conv1, sender=self.student, text="Hello 2")

        # Student 2 sends 1 message
        msg3 = send_message(conv2, sender=other_student, text="Hello from S2")

        # Teacher should have 3 unread messages
        self.assertEqual(get_total_unread_chat_count(self.teacher), 3)
        # Senders have 0 unread messages
        self.assertEqual(get_total_unread_chat_count(self.student), 0)
        self.assertEqual(get_total_unread_chat_count(other_student), 0)

        # Advance teacher watermark in conv1 for msg1
        advance_watermark(conv1, user=self.teacher, message_id=msg1.id)
        # Now teacher should have 2 unread (msg2 and msg3)
        self.assertEqual(get_total_unread_chat_count(self.teacher), 2)

        # Advance teacher watermark in conv1 for msg2
        advance_watermark(conv1, user=self.teacher, message_id=msg2.id)
        # Now teacher should have 1 unread (msg3)
        self.assertEqual(get_total_unread_chat_count(self.teacher), 1)

    def test_send_message_broadcasts_unread_chat_count_to_recipient_on_commit(self):
        conv, _ = get_or_create_conversation(self.student, self.teacher)

        with patch("chat.services.get_channel_layer") as mock_get_layer:
            mock_layer = mock_get_layer.return_value
            mock_layer.group_send = AsyncMock()

            with self.captureOnCommitCallbacks(execute=True):
                send_message(conversation=conv, sender=self.student, text="Hello Teacher")

            # Should broadcast chat_message to conversation group AND unread_chat_count to recipient's private group
            calls = mock_layer.group_send.call_args_list
            group_names = [c[0][0] for c in calls]
            payloads = [c[0][1] for c in calls]

            recipient_group = f"user_{self.teacher.id}"
            self.assertIn(recipient_group, group_names)

            unread_payload = next(p for p in payloads if p.get("type") == "unread_chat_count")
            self.assertEqual(unread_payload["count"], 1)

    def test_advance_watermark_broadcasts_unread_chat_count_when_watermark_advances(self):
        conv, _ = get_or_create_conversation(self.student, self.teacher)
        msg1 = send_message(conv, sender=self.student, text="Hello 1")
        msg2 = send_message(conv, sender=self.student, text="Hello 2")

        with patch("chat.services.get_channel_layer") as mock_get_layer:
            mock_layer = mock_get_layer.return_value
            mock_layer.group_send = AsyncMock()

            with self.captureOnCommitCallbacks(execute=True):
                advance_watermark(conv, user=self.teacher, message_id=msg1.id)

            user_group = f"user_{self.teacher.id}"
            mock_layer.group_send.assert_called_once()
            call_group = mock_layer.group_send.call_args[0][0]
            call_payload = mock_layer.group_send.call_args[0][1]

            self.assertEqual(call_group, user_group)
            self.assertEqual(call_payload["type"], "unread_chat_count")
            self.assertEqual(call_payload["count"], 1)

    def test_advance_watermark_does_not_broadcast_when_watermark_does_not_advance(self):
        conv, _ = get_or_create_conversation(self.student, self.teacher)
        msg1 = send_message(conv, sender=self.student, text="Hello 1")
        advance_watermark(conv, user=self.teacher, message_id=msg1.id)

        with patch("chat.services.get_channel_layer") as mock_get_layer:
            mock_layer = mock_get_layer.return_value
            mock_layer.group_send = AsyncMock()

            with self.captureOnCommitCallbacks(execute=True):
                # Calling with same or lower id should not broadcast
                advance_watermark(conv, user=self.teacher, message_id=msg1.id)

            mock_layer.group_send.assert_not_called()

