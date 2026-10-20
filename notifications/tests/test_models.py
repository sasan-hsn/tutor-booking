from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from notifications.models import Notification

User = get_user_model()


class NotificationModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.recipient = User.objects.create_user(
            username='recipient_user',
            email='recipient@example.com',
            password='password123',
        )
        cls.actor = User.objects.create_user(
            username='actor_user',
            email='actor@example.com',
            password='password123',
        )

    def test_create_notification_with_defaults(self):
        notif = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='New Lesson Request',
            message='You have received a new lesson request.',
        )

        self.assertEqual(notif.recipient, self.recipient)
        self.assertIsNone(notif.actor)
        self.assertEqual(notif.event_type, Notification.EventType.BOOKING_REQUESTED)
        self.assertEqual(notif.level, Notification.Level.INFO)
        self.assertEqual(notif.title, 'New Lesson Request')
        self.assertEqual(notif.message, 'You have received a new lesson request.')
        self.assertEqual(notif.action_url, '')
        self.assertEqual(notif.metadata, {})
        self.assertFalse(notif.is_read)
        self.assertIsNone(notif.read_at)
        self.assertIsNotNone(notif.created_at)

    def test_create_notification_with_all_fields(self):
        notif = Notification.objects.create(
            recipient=self.recipient,
            actor=self.actor,
            event_type=Notification.EventType.BOOKING_CONFIRMED,
            level=Notification.Level.SUCCESS,
            title='Lesson Confirmed',
            message='Your lesson was confirmed.',
            action_url='/dashboard/student/',
            metadata={'booking_id': 42},
        )

        self.assertEqual(notif.actor, self.actor)
        self.assertEqual(notif.level, Notification.Level.SUCCESS)
        self.assertEqual(notif.action_url, '/dashboard/student/')
        self.assertEqual(notif.metadata, {'booking_id': 42})

    def test_mark_as_read(self):
        notif = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='New Lesson Request',
            message='Test message',
        )
        self.assertFalse(notif.is_read)
        self.assertIsNone(notif.read_at)

        before = timezone.now()
        notif.mark_as_read()
        after = timezone.now()

        notif.refresh_from_db()
        self.assertTrue(notif.is_read)
        self.assertIsNotNone(notif.read_at)
        self.assertTrue(before <= notif.read_at <= after)

    def test_default_ordering_newest_first(self):
        notif1 = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='First',
            message='First',
        )
        notif2 = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_CONFIRMED,
            title='Second',
            message='Second',
        )

        notifications = list(Notification.objects.filter(recipient=self.recipient))
        self.assertEqual(notifications, [notif2, notif1])

    def test_composite_index_configured(self):
        index_fields = [
            tuple(idx.fields) for idx in Notification._meta.indexes
        ]
        self.assertIn(('recipient', 'is_read', '-created_at'), index_fields)

    def test_recipient_deletion_cascades(self):
        notif = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Test',
            message='Test',
        )
        self.recipient.delete()
        self.assertFalse(Notification.objects.filter(id=notif.id).exists())

    def test_actor_deletion_sets_null(self):
        notif = Notification.objects.create(
            recipient=self.recipient,
            actor=self.actor,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Test',
            message='Test',
        )
        self.actor.delete()
        notif.refresh_from_db()
        self.assertIsNone(notif.actor)

    def test_queryset_unread_helpers(self):
        notif1 = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Unread',
            message='Unread',
        )
        notif2 = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_CONFIRMED,
            title='Read',
            message='Read',
            is_read=True,
            read_at=timezone.now(),
        )

        self.assertEqual(Notification.objects.unread_count_for(self.recipient), 1)
        self.assertEqual(list(Notification.objects.unread_for(self.recipient)), [notif1])

    def test_to_ws_payload(self):
        notif = Notification.objects.create(
            recipient=self.recipient,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            level=Notification.Level.INFO,
            title='Payload Test',
            message='Testing payload generation',
            action_url='/test/',
        )
        payload = notif.to_ws_payload(unread_count=5)
        self.assertEqual(
            payload,
            {
                "type": "notification_message",
                "id": notif.id,
                "event_type": Notification.EventType.BOOKING_REQUESTED,
                "level": Notification.Level.INFO,
                "title": 'Payload Test',
                "message": 'Testing payload generation',
                "action_url": '/test/',
                "created_at": notif.created_at.isoformat(),
                "unread_count": 5,
            },
        )
