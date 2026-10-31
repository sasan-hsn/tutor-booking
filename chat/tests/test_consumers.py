import asyncio
import uuid
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.db import transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse

from chat.models import Conversation, Message
from chat.services import advance_watermark, send_message
from tutor_booking.asgi import application

User = get_user_model()


@database_sync_to_async
def create_authenticated_session(user):
    session = SessionStore()
    session["_auth_user_id"] = str(user.pk)
    session["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    session["_auth_user_hash"] = user.get_session_auth_hash()
    session.save()
    return session.session_key


async def get_authenticated_communicator(app, path, user):
    session_key = await create_authenticated_session(user)
    headers = [
        (b"cookie", f"{settings.SESSION_COOKIE_NAME}={session_key}".encode("ascii")),
    ]
    return WebsocketCommunicator(app, path, headers=headers)


class ChatConsumerIntegrationTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            username="student_alice",
            email="alice@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.teacher = User.objects.create_user(
            username="teacher_bob",
            email="bob@example.com",
            password="testpassword123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.outsider = User.objects.create_user(
            username="outsider_charlie",
            email="charlie@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.other_teacher = User.objects.create_user(
            username="teacher_dana",
            email="dana@example.com",
            password="testpassword123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )

        self.conversation = Conversation.objects.create(
            student=self.student,
            teacher=self.teacher,
        )
        self.other_conversation = Conversation.objects.create(
            student=self.outsider,
            teacher=self.other_teacher,
        )
        self.ws_path = f"/ws/chat/{self.conversation.id}/"

    def tearDown(self):
        cache.clear()

    # --- Connection Gating Tests ---

    async def test_unauthenticated_connection_rejected(self):
        communicator = WebsocketCommunicator(application, self.ws_path)
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
        await communicator.disconnect()

    async def test_unauthorized_outsider_connection_rejected(self):
        communicator = await get_authenticated_communicator(
            application, self.ws_path, self.outsider
        )
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
        await communicator.disconnect()

    async def test_nonexistent_conversation_rejected(self):
        fake_uuid = uuid.uuid4()
        communicator = await get_authenticated_communicator(
            application, f"/ws/chat/{fake_uuid}/", self.student
        )
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
        await communicator.disconnect()

    async def test_invalid_uuid_rejected(self):
        communicator = await get_authenticated_communicator(
            application, "/ws/chat/not-a-valid-uuid/", self.student
        )
        with self.assertRaises(ValueError):
            await communicator.connect()

    async def test_authorized_student_connection_accepted(self):
        communicator = await get_authenticated_communicator(
            application, self.ws_path, self.student
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_authorized_teacher_connection_accepted(self):
        communicator = await get_authenticated_communicator(
            application, self.ws_path, self.teacher
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()

    # --- Real-Time Message Streaming Tests ---

    async def test_new_message_broadcasts_to_conversation_room(self):
        student_comm = await get_authenticated_communicator(
            application, self.ws_path, self.student
        )
        connected_s, _ = await student_comm.connect()
        self.assertTrue(connected_s)

        teacher_comm = await get_authenticated_communicator(
            application, self.ws_path, self.teacher
        )
        connected_t, _ = await teacher_comm.connect()
        self.assertTrue(connected_t)

        @database_sync_to_async
        def create_message_in_transaction():
            with transaction.atomic():
                return send_message(
                    conversation=self.conversation,
                    sender=self.student,
                    text="Hello Bob! Check out https://example.com for notes.",
                )

        msg = await create_message_in_transaction()

        # Both student and teacher should receive the message frame
        frame_t = await teacher_comm.receive_json_from()
        self.assertEqual(frame_t["type"], "new_message")
        self.assertEqual(frame_t["message"]["id"], msg.id)
        self.assertEqual(frame_t["message"]["sender_id"], self.student.id)
        self.assertEqual(frame_t["message"]["sender_username"], "student_alice")
        self.assertIn("Hello Bob!", frame_t["message"]["text"])
        self.assertIn("<a href=\"https://example.com\"", frame_t["message"]["rendered_text"])

        frame_s = await student_comm.receive_json_from()
        self.assertEqual(frame_s["type"], "new_message")
        self.assertEqual(frame_s["message"]["id"], msg.id)

        await student_comm.disconnect()
        await teacher_comm.disconnect()

    async def test_http_post_send_message_triggers_websocket_broadcast(self):
        teacher_comm = await get_authenticated_communicator(
            application, self.ws_path, self.teacher
        )
        connected, _ = await teacher_comm.connect()
        self.assertTrue(connected)

        @database_sync_to_async
        def post_message_via_client():
            client = Client()
            client.force_login(self.student)
            url = reverse("chat:send_message", kwargs={"conversation_id": self.conversation.id})
            return client.post(url, {"text": "Message via HTTP POST"})

        response = await post_message_via_client()
        self.assertEqual(response.status_code, 200)

        frame = await teacher_comm.receive_json_from()
        self.assertEqual(frame["type"], "new_message")
        self.assertEqual(frame["message"]["text"], "Message via HTTP POST")
        self.assertEqual(frame["message"]["sender_id"], self.student.id)

        await teacher_comm.disconnect()

    async def test_transaction_rollback_prevents_websocket_broadcast(self):
        teacher_comm = await get_authenticated_communicator(
            application, self.ws_path, self.teacher
        )
        connected, _ = await teacher_comm.connect()
        self.assertTrue(connected)

        @database_sync_to_async
        def failed_transaction():
            try:
                with transaction.atomic():
                    send_message(
                        conversation=self.conversation,
                        sender=self.student,
                        text="This message will roll back",
                    )
                    raise RuntimeError("Simulated rollback error")
            except RuntimeError:
                pass

        await failed_transaction()

        # No frame should be received by teacher
        self.assertTrue(await teacher_comm.receive_nothing())
        await teacher_comm.disconnect()

    async def test_room_isolation_between_conversations(self):
        # Teacher is in conversation 1
        teacher_comm = await get_authenticated_communicator(
            application, self.ws_path, self.teacher
        )
        connected_t, _ = await teacher_comm.connect()
        self.assertTrue(connected_t)

        # Other teacher is in conversation 2
        other_path = f"/ws/chat/{self.other_conversation.id}/"
        other_comm = await get_authenticated_communicator(
            application, other_path, self.other_teacher
        )
        connected_o, _ = await other_comm.connect()
        self.assertTrue(connected_o)

        @database_sync_to_async
        def create_msg_in_other_conv():
            with transaction.atomic():
                return send_message(
                    conversation=self.other_conversation,
                    sender=self.outsider,
                    text="Message in other conversation",
                )

        await create_msg_in_other_conv()

        # Other teacher should receive it
        frame_o = await other_comm.receive_json_from()
        self.assertEqual(frame_o["type"], "new_message")
        self.assertEqual(frame_o["message"]["text"], "Message in other conversation")

        # Teacher Bob in conversation 1 should receive nothing
        self.assertTrue(await teacher_comm.receive_nothing())

        await teacher_comm.disconnect()
        await other_comm.disconnect()

    # --- Read Receipt & Watermark Tests ---

    async def test_advance_watermark_action_updates_db_and_broadcasts_receipt(self):
        student_comm = await get_authenticated_communicator(
            application, self.ws_path, self.student
        )
        connected_s, _ = await student_comm.connect()
        self.assertTrue(connected_s)

        teacher_comm = await get_authenticated_communicator(
            application, self.ws_path, self.teacher
        )
        connected_t, _ = await teacher_comm.connect()
        self.assertTrue(connected_t)

        @database_sync_to_async
        def create_test_message():
            with transaction.atomic():
                return send_message(
                    conversation=self.conversation,
                    sender=self.student,
                    text="Test message for read receipt",
                )

        msg = await create_test_message()
        # Drain the new_message broadcast frames
        await student_comm.receive_json_from()
        await teacher_comm.receive_json_from()

        # Teacher reads the message and sends advance_watermark action
        await teacher_comm.send_json_to({
            "action": "advance_watermark",
            "message_id": msg.id,
        })

        # Student should receive read_receipt frame
        receipt_s = await student_comm.receive_json_from()
        self.assertEqual(receipt_s["type"], "read_receipt")
        self.assertEqual(receipt_s["reader_id"], self.teacher.id)
        self.assertEqual(receipt_s["watermark"], msg.id)

        # Database should reflect advanced watermark for teacher
        @database_sync_to_async
        def get_teacher_watermark():
            self.conversation.refresh_from_db()
            return self.conversation.teacher_last_read_message_id

        watermark = await get_teacher_watermark()
        self.assertEqual(watermark, msg.id)

        await student_comm.disconnect()
        await teacher_comm.disconnect()

    async def test_advance_watermark_is_strictly_monotonic(self):
        teacher_comm = await get_authenticated_communicator(
            application, self.ws_path, self.teacher
        )
        connected, _ = await teacher_comm.connect()
        self.assertTrue(connected)

        # Advance to 10
        await teacher_comm.send_json_to({
            "action": "advance_watermark",
            "message_id": 10,
        })
        frame1 = await teacher_comm.receive_json_from()
        self.assertEqual(frame1["type"], "read_receipt")
        self.assertEqual(frame1["watermark"], 10)

        # Attempt to advance to 5 (regression)
        await teacher_comm.send_json_to({
            "action": "advance_watermark",
            "message_id": 5,
        })
        frame2 = await teacher_comm.receive_json_from()
        self.assertEqual(frame2["type"], "read_receipt")
        self.assertEqual(frame2["watermark"], 10)

        @database_sync_to_async
        def get_watermark():
            self.conversation.refresh_from_db()
            return self.conversation.teacher_last_read_message_id

        watermark = await get_watermark()
        self.assertEqual(watermark, 10)

        await teacher_comm.disconnect()

    async def test_invalid_watermark_action_does_not_crash(self):
        teacher_comm = await get_authenticated_communicator(
            application, self.ws_path, self.teacher
        )
        connected, _ = await teacher_comm.connect()
        self.assertTrue(connected)

        # Invalid message_id types/values
        await teacher_comm.send_json_to({"action": "advance_watermark", "message_id": -1})
        await teacher_comm.send_json_to({"action": "advance_watermark", "message_id": "invalid"})
        await teacher_comm.send_json_to({"action": "advance_watermark"})

        # Consumer should still be healthy
        await teacher_comm.send_json_to({"action": "advance_watermark", "message_id": 1})
        frame = await teacher_comm.receive_json_from()
        self.assertEqual(frame["type"], "read_receipt")
        self.assertEqual(frame["watermark"], 1)

        await teacher_comm.disconnect()

    # --- Typing Indicator Tests ---

    async def test_typing_indicator_broadcasts_to_counterpart(self):
        student_comm = await get_authenticated_communicator(
            application, self.ws_path, self.student
        )
        connected_s, _ = await student_comm.connect()
        self.assertTrue(connected_s)

        teacher_comm = await get_authenticated_communicator(
            application, self.ws_path, self.teacher
        )
        connected_t, _ = await teacher_comm.connect()
        self.assertTrue(connected_t)

        # Student types
        await student_comm.send_json_to({"action": "typing"})

        frame_t = await teacher_comm.receive_json_from()
        self.assertEqual(frame_t["type"], "user_typing")
        self.assertEqual(frame_t["user_id"], self.student.id)
        self.assertEqual(frame_t["username"], "student_alice")

        await student_comm.disconnect()
        await teacher_comm.disconnect()

    async def test_typing_indicator_server_side_rate_limiting(self):
        student_comm = await get_authenticated_communicator(
            application, self.ws_path, self.student
        )
        connected_s, _ = await student_comm.connect()
        self.assertTrue(connected_s)

        teacher_comm = await get_authenticated_communicator(
            application, self.ws_path, self.teacher
        )
        connected_t, _ = await teacher_comm.connect()
        self.assertTrue(connected_t)

        # Send rapid typing events (< 3.0s apart)
        await student_comm.send_json_to({"action": "typing"})
        await student_comm.send_json_to({"action": "typing"})
        await student_comm.send_json_to({"action": "typing"})

        # Teacher should receive the first typing frame
        frame = await teacher_comm.receive_json_from()
        self.assertEqual(frame["type"], "user_typing")

        # The subsequent rapid typing frames must be dropped by the rate limiter
        self.assertTrue(await teacher_comm.receive_nothing())

        await student_comm.disconnect()
        await teacher_comm.disconnect()

    async def test_non_dict_json_frame_handled_gracefully(self):
        student_comm = await get_authenticated_communicator(
            application, self.ws_path, self.student
        )
        connected, _ = await student_comm.connect()
        self.assertTrue(connected)

        await student_comm.send_json_to(["list", "of", "items"])
        await student_comm.send_json_to("plain string frame")
        await student_comm.send_json_to(12345)

        # Still responsive
        await student_comm.send_json_to({"action": "typing"})
        frame = await student_comm.receive_json_from()
        self.assertEqual(frame["type"], "user_typing")

        await student_comm.disconnect()
