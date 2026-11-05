import uuid
from unittest.mock import MagicMock, patch
from anymail.exceptions import AnymailAPIError
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from chat.models import Conversation, Message
from chat.presence import (
    add_room_presence,
    get_presence_key,
    is_user_in_room,
    remove_room_presence,
)
from chat.services import (
    advance_watermark,
    clear_digest_debounce_flag,
    schedule_unread_message_digest,
    send_message,
)
from chat.tasks import (
    send_unread_message_digest,
)

User = get_user_model()


class ChatPresenceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.conv_id = uuid.uuid4()
        self.user_id = 42

    def test_presence_lifecycle(self):
        self.assertFalse(is_user_in_room(self.conv_id, self.user_id))

        add_room_presence(self.conv_id, self.user_id, "chan_1")
        self.assertTrue(is_user_in_room(self.conv_id, self.user_id))

        # Add second channel for same user
        add_room_presence(self.conv_id, self.user_id, "chan_2")
        self.assertTrue(is_user_in_room(self.conv_id, self.user_id))

        # Removing first channel should keep presence active
        remove_room_presence(self.conv_id, self.user_id, "chan_1")
        self.assertTrue(is_user_in_room(self.conv_id, self.user_id))

        # Removing second channel clears presence
        remove_room_presence(self.conv_id, self.user_id, "chan_2")
        self.assertFalse(is_user_in_room(self.conv_id, self.user_id))

    def test_presence_key_format(self):
        key = get_presence_key(self.conv_id, self.user_id)
        self.assertEqual(key, f"chat_presence:{self.conv_id}:{self.user_id}")


class ChatDebounceAndSchedulingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            username="student_alice",
            email="alice@example.com",
            first_name="Alice",
            last_name="Smith",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.teacher = User.objects.create_user(
            username="teacher_bob",
            email="bob@example.com",
            first_name="Bob",
            last_name="Jones",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.conversation = Conversation.objects.create(
            student=self.student,
            teacher=self.teacher,
        )

    @patch("chat.tasks.send_unread_message_digest.apply_async")
    def test_message_to_offline_recipient_sets_debounce_flag_and_schedules_task(
        self, mock_apply_async
    ):
        expected_flag = f"chat_digest_pending:{self.teacher.id}:{self.conversation.id}"
        self.assertIsNone(cache.get(expected_flag))

        with self.captureOnCommitCallbacks(execute=True):
            send_message(self.conversation, sender=self.student, text="Hello teacher!")

        self.assertTrue(cache.get(expected_flag))
        mock_apply_async.assert_called_once_with(
            args=[str(self.conversation.id), self.teacher.id],
            countdown=900,
        )

    @patch("chat.tasks.send_unread_message_digest.apply_async")
    def test_subsequent_messages_do_not_schedule_redundant_tasks(
        self, mock_apply_async
    ):
        expected_flag = f"chat_digest_pending:{self.teacher.id}:{self.conversation.id}"

        # First message schedules task
        with self.captureOnCommitCallbacks(execute=True):
            send_message(self.conversation, sender=self.student, text="Message 1")
        self.assertEqual(mock_apply_async.call_count, 1)
        self.assertTrue(cache.get(expected_flag))

        # Second rapid message within 15 min does not schedule new task
        with self.captureOnCommitCallbacks(execute=True):
            send_message(self.conversation, sender=self.student, text="Message 2")
        self.assertEqual(mock_apply_async.call_count, 1)

        # Third rapid message also debounced
        with self.captureOnCommitCallbacks(execute=True):
            send_message(self.conversation, sender=self.student, text="Message 3")
        self.assertEqual(mock_apply_async.call_count, 1)

    @patch("chat.tasks.send_unread_message_digest.apply_async")
    def test_message_to_online_recipient_does_not_schedule_task(
        self, mock_apply_async
    ):
        expected_flag = f"chat_digest_pending:{self.teacher.id}:{self.conversation.id}"

        # Recipient is currently in the room
        add_room_presence(self.conversation.id, self.teacher.id, "active_ws_chan")
        self.assertTrue(is_user_in_room(self.conversation.id, self.teacher.id))

        with self.captureOnCommitCallbacks(execute=True):
            send_message(self.conversation, sender=self.student, text="Are you there?")

        self.assertEqual(mock_apply_async.call_count, 0)
        self.assertIsNone(cache.get(expected_flag))

    def test_advancing_watermark_clears_debounce_flag(self):
        expected_flag = f"chat_digest_pending:{self.teacher.id}:{self.conversation.id}"
        cache.set(expected_flag, True, timeout=900)
        self.assertTrue(cache.get(expected_flag))

        msg = send_message(self.conversation, sender=self.student, text="Check this out")
        # Ensure flag is set
        cache.set(expected_flag, True, timeout=900)

        # Teacher advances watermark
        advance_watermark(self.conversation, self.teacher, msg.id)
        self.assertIsNone(cache.get(expected_flag))

    def test_conversation_detail_view_clears_debounce_flag(self):
        expected_flag = f"chat_digest_pending:{self.teacher.id}:{self.conversation.id}"
        cache.set(expected_flag, True, timeout=900)

        msg = send_message(self.conversation, sender=self.student, text="Hello!")
        cache.set(expected_flag, True, timeout=900)

        self.client.force_login(self.teacher)
        response = self.client.get(
            reverse("chat:conversation_detail", kwargs={"conversation_id": self.conversation.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(cache.get(expected_flag))


class SendUnreadMessageDigestWorkerTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.student = User.objects.create_user(
            username="student_alice",
            email="alice@example.com",
            first_name="Alice",
            last_name="Smith",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.teacher = User.objects.create_user(
            username="teacher_bob",
            email="bob@example.com",
            first_name="Bob",
            last_name="Jones",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.conversation = Conversation.objects.create(
            student=self.student,
            teacher=self.teacher,
        )

    def test_digest_task_sends_email_when_unread_and_offline(self):
        msg1 = send_message(self.conversation, sender=self.student, text="Hi Bob, lesson question.")
        msg2 = send_message(self.conversation, sender=self.student, text="Also, see this attachment.")

        # Recipient is offline
        self.assertFalse(is_user_in_room(self.conversation.id, self.teacher.id))

        # Execute Celery worker task directly
        send_unread_message_digest(self.conversation.id, self.teacher.id)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, [self.teacher.email])
        self.assertIn("Alice Smith", email.subject)
        self.assertIn("2 new messages", email.subject)

        # Check content
        conv_url = f"/messages/{self.conversation.id}/"
        self.assertIn("Alice Smith", email.body)
        self.assertIn("2 unread messages", email.body)
        self.assertIn("Hi Bob, lesson question.", email.body)
        self.assertIn("Also, see this attachment.", email.body)
        self.assertIn(conv_url, email.body)

        # Check multipart HTML alternative
        self.assertEqual(len(email.alternatives), 1)
        html_content, mimetype = email.alternatives[0]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("Alice Smith", html_content)
        self.assertIn("2", html_content)
        self.assertIn(conv_url, html_content)

    def test_digest_task_singular_subject_for_single_message(self):
        send_message(self.conversation, sender=self.student, text="Just one quick note.")

        send_unread_message_digest(self.conversation.id, self.teacher.id)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("New message from Alice Smith", email.subject)
        self.assertIn("1 unread message", email.body)

    def test_digest_task_suppressed_when_all_messages_read(self):
        msg = send_message(self.conversation, sender=self.student, text="Read me!")
        advance_watermark(self.conversation, self.teacher, msg.id)

        send_unread_message_digest(self.conversation.id, self.teacher.id)

        self.assertEqual(len(mail.outbox), 0)

    def test_digest_task_suppressed_when_recipient_active_in_room(self):
        send_message(self.conversation, sender=self.student, text="Are you watching?")
        add_room_presence(self.conversation.id, self.teacher.id, "active_conn")

        send_unread_message_digest(self.conversation.id, self.teacher.id)

        # Active presence suppression: no email sent!
        self.assertEqual(len(mail.outbox), 0)

    def test_digest_task_clears_debounce_cache_flag_on_completion(self):
        flag = f"chat_digest_pending:{self.teacher.id}:{self.conversation.id}"
        cache.set(flag, True, timeout=900)

        send_message(self.conversation, sender=self.student, text="Testing flag clearing")
        send_unread_message_digest(self.conversation.id, self.teacher.id)

        self.assertIsNone(cache.get(flag))

    def test_digest_task_skips_when_no_unread_from_counterpart(self):
        # Only teacher's own messages exist
        send_message(self.conversation, sender=self.teacher, text="Self sent note")

        send_unread_message_digest(self.conversation.id, self.teacher.id)
        self.assertEqual(len(mail.outbox), 0)

    def test_digest_task_handles_invalid_ids_gracefully(self):
        fake_uuid = uuid.uuid4()
        # Should not raise exception
        send_unread_message_digest(fake_uuid, self.teacher.id)
        send_unread_message_digest(self.conversation.id, 99999)
        self.assertEqual(len(mail.outbox), 0)

    @patch("chat.tasks.send_chat_unread_digest_email")
    def test_digest_task_transient_error_retries(self, mock_send_email):
        send_message(self.conversation, sender=self.student, text="Retry me")
        transient_exc = ConnectionError("Network timeout")
        mock_send_email.side_effect = transient_exc

        with patch.object(
            send_unread_message_digest, "retry", side_effect=RuntimeError("RetryCalled")
        ) as mock_retry:
            with self.assertRaises(RuntimeError):
                send_unread_message_digest(self.conversation.id, self.teacher.id)

            mock_retry.assert_called_once()
