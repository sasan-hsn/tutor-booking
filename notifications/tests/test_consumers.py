from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import transaction
from django.test import TransactionTestCase

from notifications.models import Notification
from notifications.services import create_and_send_notification
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


class NotificationConsumerTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ws_user",
            email="ws_user@example.com",
            password="password123",
        )
        self.other_user = User.objects.create_user(
            username="other_ws_user",
            email="other_ws_user@example.com",
            password="password123",
        )
        self.teacher_user = User.objects.create_user(
            username="ws_teacher",
            email="ws_teacher@example.com",
            password="password123",
            role=User.Role.TEACHER,
        )

    async def test_unauthenticated_connection_rejected(self):
        communicator = WebsocketCommunicator(application, "/ws/notifications/")
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
        await communicator.disconnect()

    async def test_authenticated_connection_accepted(self):
        communicator = await get_authenticated_communicator(
            application, "/ws/notifications/", self.user
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_get_unread_count_action(self):
        await Notification.objects.acreate(
            recipient=self.user,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title="Unread 1",
            message="Message 1",
        )
        await Notification.objects.acreate(
            recipient=self.user,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title="Unread 2",
            message="Message 2",
        )

        communicator = await get_authenticated_communicator(
            application, "/ws/notifications/", self.user
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to({"action": "get_unread_count"})
        response = await communicator.receive_json_from()
        self.assertEqual(response, {"type": "unread_count", "count": 2})

        await communicator.disconnect()

    async def test_non_dict_json_frame_does_not_crash_consumer(self):
        communicator = await get_authenticated_communicator(
            application, "/ws/notifications/", self.user
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        # Send a JSON list instead of a dict
        await communicator.send_json_to(["action", "get_unread_count"])
        # Send a JSON string instead of a dict
        await communicator.send_json_to("get_unread_count")

        # The connection should remain open and healthy
        await communicator.send_json_to({"action": "get_unread_count"})
        response = await communicator.receive_json_from()
        self.assertEqual(response, {"type": "unread_count", "count": 0})

        await communicator.disconnect()

    async def test_receives_broadcast_frame_on_private_group(self):
        communicator1 = await get_authenticated_communicator(
            application, "/ws/notifications/", self.user
        )
        connected1, _ = await communicator1.connect()
        self.assertTrue(connected1)

        communicator2 = await get_authenticated_communicator(
            application, "/ws/notifications/", self.other_user
        )
        connected2, _ = await communicator2.connect()
        self.assertTrue(connected2)

        channel_layer = get_channel_layer()
        payload = {
            "type": "notification_message",
            "id": 42,
            "event_type": "booking_requested",
            "level": "info",
            "title": "New Booking",
            "message": "Student booked a lesson.",
            "action_url": "/dashboard/teacher/",
            "created_at": "2026-10-19T12:00:00Z",
            "unread_count": 1,
        }

        # Broadcast only to self.user's group
        await channel_layer.group_send(
            Notification.get_group_name(self.user.id), payload
        )

        response = await communicator1.receive_json_from()
        self.assertEqual(response, payload)

        # Ensure other user did not receive the broadcast
        self.assertTrue(await communicator2.receive_nothing())

        await communicator1.disconnect()
        await communicator2.disconnect()

    async def test_receives_unread_chat_count_broadcast(self):
        communicator = await get_authenticated_communicator(
            application, "/ws/notifications/", self.user
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        channel_layer = get_channel_layer()
        payload = {
            "type": "unread_chat_count",
            "count": 4,
        }

        await channel_layer.group_send(
            Notification.get_group_name(self.user.id), payload
        )

        response = await communicator.receive_json_from()
        self.assertEqual(response, payload)

        await communicator.disconnect()

    async def test_receives_enhanced_unread_chat_metadata_broadcast(self):
        communicator = await get_authenticated_communicator(
            application, "/ws/notifications/", self.teacher_user
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        channel_layer = get_channel_layer()
        payload = {
            "type": "unread_chat_count",
            "count": 2,
            "unread_count": 2,
            "conversation_id": "12345678-1234-5678-1234-567812345678",
            "conversation_url": "/messages/12345678-1234-5678-1234-567812345678/",
            "snippet": "Can we reschedule to 3pm?",
            "sender_name": "Alice Smith",
            "sender_initial": "A",
            "sender_avatar_url": "",
            "sender_color": "#2563eb",
            "timestamp": "2026-11-20T10:00:00Z",
        }

        await channel_layer.group_send(
            Notification.get_group_name(self.teacher_user.id), payload
        )

        response = await communicator.receive_json_from()
        self.assertEqual(response, payload)

        await communicator.disconnect()

    async def test_end_to_end_send_message_emission_to_notification_websocket(self):
        communicator = await get_authenticated_communicator(
            application, "/ws/notifications/", self.teacher_user
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        @database_sync_to_async
        def create_and_send():
            from chat.services import get_or_create_conversation, send_message

            with transaction.atomic():
                conv, _ = get_or_create_conversation(self.user, self.teacher_user)
                send_message(
                    conversation=conv,
                    sender=self.user,
                    text="Hello from student!",
                )
            return conv.id

        conv_id = await create_and_send()

        frame = await communicator.receive_json_from()
        self.assertEqual(frame["type"], "unread_chat_count")
        self.assertEqual(frame["count"], 1)
        self.assertEqual(frame["unread_count"], 1)
        self.assertEqual(frame["conversation_id"], str(conv_id))
        self.assertEqual(frame["snippet"], "Hello from student!")
        self.assertEqual(
            frame["sender_name"],
            self.user.get_full_name() or self.user.username,
        )
        self.assertIn("timestamp", frame)
        self.assertIn("conversation_url", frame)
        self.assertIn("sender_color", frame)
        self.assertIn("sender_initial", frame)

        await communicator.disconnect()

    async def test_get_unread_chat_count_action(self):
        communicator = await get_authenticated_communicator(
            application, "/ws/notifications/", self.user
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to({"action": "get_unread_chat_count"})
        response = await communicator.receive_json_from()
        self.assertEqual(response, {"type": "unread_chat_count", "count": 0})

        await communicator.disconnect()

    async def test_end_to_end_service_emission_to_websocket_communicator(self):
        communicator = await get_authenticated_communicator(
            application, "/ws/notifications/", self.user
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        @database_sync_to_async
        def create_notification_in_transaction():
            with transaction.atomic():
                notif = create_and_send_notification(
                    recipient=self.user,
                    event_type=Notification.EventType.BOOKING_CONFIRMED,
                    title="Confirmed Lesson",
                    message="Lesson has been confirmed.",
                    level=Notification.Level.SUCCESS,
                    action_url="/dashboard/student/",
                )
            return notif.id

        notif_id = await create_notification_in_transaction()

        frame = await communicator.receive_json_from()
        self.assertEqual(frame["type"], "notification_message")
        self.assertEqual(frame["id"], notif_id)
        self.assertEqual(frame["event_type"], Notification.EventType.BOOKING_CONFIRMED)
        self.assertEqual(frame["level"], Notification.Level.SUCCESS)
        self.assertEqual(frame["title"], "Confirmed Lesson")
        self.assertEqual(frame["message"], "Lesson has been confirmed.")
        self.assertEqual(frame["action_url"], "/dashboard/student/")
        self.assertEqual(frame["unread_count"], 1)

        await communicator.disconnect()

    async def test_channel_layer_timeout_or_error_does_not_abort_handshake(self):
        from unittest.mock import patch

        channel_layer = get_channel_layer()

        async def failing_group_add(*args, **kwargs):
            raise TimeoutError("Timeout reading from localhost:6379")

        communicator = await get_authenticated_communicator(
            application, "/ws/notifications/", self.user
        )
        with patch.object(channel_layer, "group_add", side_effect=failing_group_add):
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # Can still communicate with the consumer (e.g. database-backed actions)
            await communicator.send_json_to({"action": "get_unread_count"})
            response = await communicator.receive_json_from()
            self.assertEqual(response, {"type": "unread_count", "count": 0})

        await communicator.disconnect()

    async def test_channel_layer_receive_timeout_resilience_preserves_connection(self):
        import asyncio
        from unittest.mock import patch

        from redis.exceptions import TimeoutError as RedisTimeoutError

        channel_layer = get_channel_layer()
        original_receive = channel_layer.receive
        timed_out_once = False

        async def failing_receive_once(channel_name):
            nonlocal timed_out_once
            if not timed_out_once:
                timed_out_once = True
                raise RedisTimeoutError("Timeout reading from 127.0.0.1:6379")
            return await original_receive(channel_name)

        with patch.object(channel_layer, "receive", side_effect=failing_receive_once):
            communicator = await get_authenticated_communicator(
                application, "/ws/notifications/", self.user
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # Wait briefly for the first receive call to timeout and recover
            await asyncio.sleep(0.3)

            # Broadcast a notification to verify consumer receives it after the timeout recovery
            payload = {
                "type": "notification_message",
                "id": 100,
                "event_type": "booking_confirmed",
                "level": "success",
                "title": "Recovered Lesson",
                "message": "Message after timeout",
                "action_url": "",
                "created_at": "2026-10-21T10:00:00Z",
                "unread_count": 1,
            }
            await channel_layer.group_send(
                Notification.get_group_name(self.user.id), payload
            )

            response = await communicator.receive_json_from()
            self.assertEqual(response, payload)

            await communicator.disconnect()
