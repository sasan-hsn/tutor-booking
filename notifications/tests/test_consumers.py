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
    session['_auth_user_id'] = str(user.pk)
    session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
    session['_auth_user_hash'] = user.get_session_auth_hash()
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
            username='ws_user',
            email='ws_user@example.com',
            password='password123',
        )
        self.other_user = User.objects.create_user(
            username='other_ws_user',
            email='other_ws_user@example.com',
            password='password123',
        )

    async def test_unauthenticated_connection_rejected(self):
        communicator = WebsocketCommunicator(application, "/ws/notifications/")
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
        await communicator.disconnect()

    async def test_authenticated_connection_accepted(self):
        communicator = await get_authenticated_communicator(application, "/ws/notifications/", self.user)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_get_unread_count_action(self):
        await Notification.objects.acreate(
            recipient=self.user,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Unread 1',
            message='Message 1',
        )
        await Notification.objects.acreate(
            recipient=self.user,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Unread 2',
            message='Message 2',
        )

        communicator = await get_authenticated_communicator(application, "/ws/notifications/", self.user)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to({"action": "get_unread_count"})
        response = await communicator.receive_json_from()
        self.assertEqual(response, {"type": "unread_count", "count": 2})

        await communicator.disconnect()

    async def test_non_dict_json_frame_does_not_crash_consumer(self):
        communicator = await get_authenticated_communicator(application, "/ws/notifications/", self.user)
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
        communicator1 = await get_authenticated_communicator(application, "/ws/notifications/", self.user)
        connected1, _ = await communicator1.connect()
        self.assertTrue(connected1)

        communicator2 = await get_authenticated_communicator(application, "/ws/notifications/", self.other_user)
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
        await channel_layer.group_send(Notification.get_group_name(self.user.id), payload)

        response = await communicator1.receive_json_from()
        self.assertEqual(response, payload)

        # Ensure other user did not receive the broadcast
        self.assertTrue(await communicator2.receive_nothing())

        await communicator1.disconnect()
        await communicator2.disconnect()

    async def test_end_to_end_service_emission_to_websocket_communicator(self):
        communicator = await get_authenticated_communicator(application, "/ws/notifications/", self.user)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        @database_sync_to_async
        def create_notification_in_transaction():
            with transaction.atomic():
                notif = create_and_send_notification(
                    recipient=self.user,
                    event_type=Notification.EventType.BOOKING_CONFIRMED,
                    title='Confirmed Lesson',
                    message='Lesson has been confirmed.',
                    level=Notification.Level.SUCCESS,
                    action_url='/dashboard/student/',
                )
            return notif.id

        notif_id = await create_notification_in_transaction()

        frame = await communicator.receive_json_from()
        self.assertEqual(frame['type'], 'notification_message')
        self.assertEqual(frame['id'], notif_id)
        self.assertEqual(frame['event_type'], Notification.EventType.BOOKING_CONFIRMED)
        self.assertEqual(frame['level'], Notification.Level.SUCCESS)
        self.assertEqual(frame['title'], 'Confirmed Lesson')
        self.assertEqual(frame['message'], 'Lesson has been confirmed.')
        self.assertEqual(frame['action_url'], '/dashboard/student/')
        self.assertEqual(frame['unread_count'], 1)

        await communicator.disconnect()

    async def test_channel_layer_timeout_or_error_does_not_abort_handshake(self):
        from unittest.mock import patch

        channel_layer = get_channel_layer()

        async def failing_group_add(*args, **kwargs):
            raise TimeoutError("Timeout reading from localhost:6379")

        communicator = await get_authenticated_communicator(application, "/ws/notifications/", self.user)
        with patch.object(channel_layer, "group_add", side_effect=failing_group_add):
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # Can still communicate with the consumer (e.g. database-backed actions)
            await communicator.send_json_to({"action": "get_unread_count"})
            response = await communicator.receive_json_from()
            self.assertEqual(response, {"type": "unread_count", "count": 0})

        await communicator.disconnect()
