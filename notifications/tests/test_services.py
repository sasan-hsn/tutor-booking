from unittest.mock import AsyncMock, patch
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase

from notifications.models import Notification
from notifications.services import create_and_send_notification

User = get_user_model()


class CreateAndSendNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.recipient = User.objects.create_user(
            username='recipient_service',
            email='recipient_service@example.com',
            password='password123',
        )
        cls.actor = User.objects.create_user(
            username='actor_service',
            email='actor_service@example.com',
            password='password123',
        )

    def test_creates_notification_record(self):
        notif = create_and_send_notification(
            recipient=self.recipient,
            actor=self.actor,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            level=Notification.Level.INFO,
            title='New Lesson Request',
            message='Sarah requested a lesson.',
            action_url='/booking/teacher/dashboard/',
            metadata={'booking_id': 101},
        )

        self.assertIsNotNone(notif.pk)
        self.assertEqual(notif.recipient, self.recipient)
        self.assertEqual(notif.actor, self.actor)
        self.assertEqual(notif.event_type, Notification.EventType.BOOKING_REQUESTED)
        self.assertEqual(notif.title, 'New Lesson Request')
        self.assertEqual(notif.metadata, {'booking_id': 101})

    def test_broadcasts_to_channel_layer_on_commit(self):
        with patch('notifications.services.get_channel_layer') as mock_get_layer:
            mock_layer = mock_get_layer.return_value
            mock_layer.group_send = AsyncMock()
            with self.captureOnCommitCallbacks(execute=True):
                notif = create_and_send_notification(
                    recipient=self.recipient,
                    event_type=Notification.EventType.BOOKING_CONFIRMED,
                    title='Lesson Confirmed',
                    message='Your lesson is confirmed.',
                    level=Notification.Level.SUCCESS,
                    action_url='/dashboard/student/',
                )

            mock_layer.group_send.assert_awaited_once()
            call_args = mock_layer.group_send.call_args
            group_name = call_args[0][0]
            payload = call_args[0][1]

            self.assertEqual(group_name, Notification.get_group_name(self.recipient.id))
            self.assertEqual(payload['type'], 'notification_message')
            self.assertEqual(payload['id'], notif.id)
            self.assertEqual(payload['event_type'], Notification.EventType.BOOKING_CONFIRMED)
            self.assertEqual(payload['level'], Notification.Level.SUCCESS)
            self.assertEqual(payload['title'], 'Lesson Confirmed')
            self.assertEqual(payload['message'], 'Your lesson is confirmed.')
            self.assertEqual(payload['action_url'], '/dashboard/student/')
            self.assertEqual(payload['unread_count'], 1)

    def test_unread_count_reflects_all_unread_records_on_commit(self):
        Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Existing 1',
            message='Unread message 1',
        )
        Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Existing 2',
            message='Unread message 2',
        )

        with patch('notifications.services.get_channel_layer') as mock_get_layer:
            mock_layer = mock_get_layer.return_value
            mock_layer.group_send = AsyncMock()
            with self.captureOnCommitCallbacks(execute=True):
                create_and_send_notification(
                    recipient=self.recipient,
                    event_type=Notification.EventType.BOOKING_REQUESTED,
                    title='New Notification',
                    message='Third unread',
                )

            call_args = mock_layer.group_send.call_args
            payload = call_args[0][1]
            self.assertEqual(payload['unread_count'], 3)

    def test_no_broadcast_when_transaction_rolls_back(self):
        with patch('notifications.services.get_channel_layer') as mock_get_layer:
            mock_layer = mock_get_layer.return_value
            try:
                with transaction.atomic():
                    create_and_send_notification(
                        recipient=self.recipient,
                        event_type=Notification.EventType.BOOKING_REQUESTED,
                        title='Rollback Test',
                        message='Should not be sent',
                    )
                    raise RuntimeError("Forced rollback")
            except RuntimeError:
                pass

            mock_layer.group_send.assert_not_called()
            self.assertFalse(Notification.objects.filter(title='Rollback Test').exists())

    def test_channel_layer_error_does_not_crash_caller(self):
        with patch('notifications.services.get_channel_layer') as mock_get_layer:
            mock_layer = mock_get_layer.return_value
            mock_layer.group_send = AsyncMock(side_effect=Exception("Redis connection error"))
            with self.captureOnCommitCallbacks(execute=True):
                notif = create_and_send_notification(
                    recipient=self.recipient,
                    event_type=Notification.EventType.BOOKING_CONFIRMED,
                    title='Safe Send Test',
                    message='Error suppressed',
                )

            self.assertIsNotNone(notif.pk)
            mock_layer.group_send.assert_awaited_once()
