import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from chat.models import Conversation, Message

User = get_user_model()


class ConversationModelTests(TestCase):
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

    def test_conversation_creation_defaults(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        self.assertIsInstance(conv.id, uuid.UUID)
        self.assertEqual(conv.student_last_read_message_id, 0)
        self.assertEqual(conv.teacher_last_read_message_id, 0)
        self.assertIsNone(conv.last_message_at)
        self.assertEqual(conv.last_message_snippet, "")
        self.assertIsNotNone(conv.created_at)
        self.assertIsNotNone(conv.updated_at)

    def test_unique_student_teacher_constraint(self):
        Conversation.objects.create(student=self.student, teacher=self.teacher)
        dup = Conversation(student=self.student, teacher=self.teacher)
        with self.assertRaises(ValidationError):
            dup.full_clean()
        with self.assertRaises(IntegrityError):
            Conversation.objects.bulk_create([dup])

    def test_prevent_self_conversation_database_constraint(self):
        self_conv = Conversation(student=self.teacher, teacher=self.teacher)
        with self.assertRaises(ValidationError):
            self_conv.full_clean()
        with self.assertRaises(IntegrityError):
            Conversation.objects.bulk_create([self_conv])

    def test_clean_validation_prevent_self_conversation(self):
        conv = Conversation(student=self.student, teacher=self.student)
        with self.assertRaises(ValidationError) as ctx:
            conv.full_clean()
        self.assertIn("Self-conversations are not permitted", str(ctx.exception))

    def test_clean_validation_requires_correct_roles(self):
        other_student = User.objects.create_user(
            username="student_two",
            email="student2@example.com",
            role=User.Role.STUDENT,
        )
        conv_invalid_teacher = Conversation(student=self.student, teacher=other_student)
        with self.assertRaises(ValidationError) as ctx:
            conv_invalid_teacher.full_clean()
        self.assertIn("Teacher must have the teacher role", str(ctx.exception))

        other_teacher = User.objects.create_user(
            username="teacher_two",
            email="teacher2@example.com",
            role=User.Role.TEACHER,
        )
        conv_invalid_student = Conversation(student=other_teacher, teacher=self.teacher)
        with self.assertRaises(ValidationError) as ctx:
            conv_invalid_student.full_clean()
        self.assertIn("Student must have the student role", str(ctx.exception))

    def test_get_counterpart(self):
        conv = Conversation.objects.create(student=self.student, teacher=self.teacher)
        self.assertEqual(conv.get_counterpart(self.student), self.teacher)
        self.assertEqual(conv.get_counterpart(self.teacher), self.student)

        outsider = User.objects.create_user(username="outsider", email="outsider@example.com")
        with self.assertRaises(ValueError):
            conv.get_counterpart(outsider)

    def test_get_last_read_message_id(self):
        conv = Conversation.objects.create(
            student=self.student,
            teacher=self.teacher,
            student_last_read_message_id=12,
            teacher_last_read_message_id=45,
        )
        self.assertEqual(conv.get_last_read_message_id(self.student), 12)
        self.assertEqual(conv.get_last_read_message_id(self.teacher), 45)


class MessageModelTests(TestCase):
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
        self.conversation = Conversation.objects.create(
            student=self.student,
            teacher=self.teacher,
        )

    def test_message_creation_defaults(self):
        msg = Message.objects.create(
            conversation=self.conversation,
            sender=self.student,
            text="Hello teacher!",
        )
        self.assertIsInstance(msg.id, int)
        self.assertEqual(msg.message_type, Message.MessageType.TEXT)
        self.assertEqual(msg.metadata, {})
        self.assertIsNotNone(msg.created_at)

    def test_message_clean_whitespace_rejected_for_text(self):
        msg = Message(
            conversation=self.conversation,
            sender=self.student,
            message_type=Message.MessageType.TEXT,
            text="   \n \t  ",
        )
        with self.assertRaises(ValidationError) as ctx:
            msg.full_clean()
        self.assertIn("Message text cannot be empty or whitespace only", str(ctx.exception))

    def test_message_max_length_2000(self):
        msg = Message(
            conversation=self.conversation,
            sender=self.student,
            text="a" * 2000,
        )
        msg.full_clean()  # Should not raise

        msg_too_long = Message(
            conversation=self.conversation,
            sender=self.student,
            text="a" * 2001,
        )
        with self.assertRaises(ValidationError):
            msg_too_long.full_clean()

    def test_message_sender_must_be_participant(self):
        outsider = User.objects.create_user(
            username="outsider",
            email="outsider@example.com",
            role=User.Role.STUDENT,
        )
        msg = Message(
            conversation=self.conversation,
            sender=outsider,
            text="Intruder message",
        )
        with self.assertRaises(ValidationError) as ctx:
            msg.full_clean()
        self.assertIn("Sender must be a participant of the conversation", str(ctx.exception))
