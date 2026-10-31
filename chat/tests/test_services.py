from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from chat.models import Conversation, Message
from chat.services import (
    advance_watermark,
    get_or_create_conversation,
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
