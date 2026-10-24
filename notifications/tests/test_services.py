from unittest.mock import AsyncMock, patch
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase

from notifications.models import Notification
from notifications.services import (
    create_and_send_notification,
    mark_booking_notifications_as_read,
)

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


class MarkBookingNotificationsAsReadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.recipient = User.objects.create_user(
            username='recipient_mark_read',
            email='recipient_mark_read@example.com',
            password='password123',
        )
        cls.other_user = User.objects.create_user(
            username='other_mark_read',
            email='other_mark_read@example.com',
            password='password123',
        )

    def test_mark_booking_notifications_as_read_success(self):
        notif1 = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Request 1',
            message='Please accept',
            metadata={'booking_id': 101},
        )
        notif2 = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.CANCELLATION_REQUESTED,
            title='Cancel Request',
            message='Please cancel',
            metadata={'booking_id': 101},
        )
        notif_other_booking = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Request 2',
            message='Another booking',
            metadata={'booking_id': 102},
        )

        with patch('notifications.services.get_channel_layer') as mock_get_layer:
            mock_layer = mock_get_layer.return_value
            mock_layer.group_send = AsyncMock()
            with self.captureOnCommitCallbacks(execute=True):
                marked_ids, unread_count = mark_booking_notifications_as_read(
                    recipient=self.recipient,
                    booking_id=101,
                )

        notif1.refresh_from_db()
        notif2.refresh_from_db()
        notif_other_booking.refresh_from_db()

        self.assertTrue(notif1.is_read)
        self.assertIsNotNone(notif1.read_at)
        self.assertTrue(notif2.is_read)
        self.assertIsNotNone(notif2.read_at)
        self.assertFalse(notif_other_booking.is_read)

        self.assertCountEqual(marked_ids, [notif1.id, notif2.id])
        self.assertEqual(unread_count, 1)

        mock_layer.group_send.assert_awaited_once()
        call_args = mock_layer.group_send.call_args
        group_name = call_args[0][0]
        payload = call_args[0][1]

        self.assertEqual(group_name, Notification.get_group_name(self.recipient.id))
        self.assertEqual(payload['type'], 'notification_read')
        self.assertCountEqual(payload['notification_ids'], [notif1.id, notif2.id])
        self.assertEqual(payload['unread_count'], 1)

    def test_mark_booking_notifications_filtered_by_event_type(self):
        notif1 = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Request 1',
            message='Please accept',
            metadata={'booking_id': 101},
        )
        notif2 = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_CONFIRMED,
            title='Confirmed',
            message='Lesson confirmed',
            metadata={'booking_id': 101},
        )

        marked_ids, unread_count = mark_booking_notifications_as_read(
            recipient=self.recipient,
            booking_id=101,
            event_types=[Notification.EventType.BOOKING_REQUESTED],
        )

        notif1.refresh_from_db()
        notif2.refresh_from_db()

        self.assertTrue(notif1.is_read)
        self.assertFalse(notif2.is_read)
        self.assertEqual(marked_ids, [notif1.id])
        self.assertEqual(unread_count, 1)

    def test_mark_booking_notifications_when_none_match(self):
        with patch('notifications.services.get_channel_layer') as mock_get_layer:
            mock_layer = mock_get_layer.return_value
            mock_layer.group_send = AsyncMock()
            with self.captureOnCommitCallbacks(execute=True):
                marked_ids, unread_count = mark_booking_notifications_as_read(
                    recipient=self.recipient,
                    booking_id=999,
                )

        self.assertEqual(marked_ids, [])
        self.assertEqual(unread_count, 0)
        mock_layer.group_send.assert_not_called()

    def test_mark_booking_notifications_ignores_other_recipient(self):
        notif_other_user = Notification.objects.create(
            recipient=self.other_user,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Other User Request',
            message='Please accept',
            metadata={'booking_id': 101},
        )

        marked_ids, unread_count = mark_booking_notifications_as_read(
            recipient=self.recipient,
            booking_id=101,
        )

        notif_other_user.refresh_from_db()
        self.assertFalse(notif_other_user.is_read)
        self.assertEqual(marked_ids, [])
        self.assertEqual(unread_count, 0)

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
