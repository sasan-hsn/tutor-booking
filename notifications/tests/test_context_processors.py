from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from notifications.context_processors import notification_context
from notifications.models import Notification

User = get_user_model()


class NotificationContextProcessorTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()

    def test_anonymous_user_returns_empty_notifications_and_zero_unread(self):
        from django.contrib.auth.models import AnonymousUser

        request = self.factory.get('/')
        request.user = AnonymousUser()

        context = notification_context(request)
        self.assertEqual(list(context['recent_notifications']), [])
        self.assertEqual(context['unread_notifications_count'], 0)

    def test_authenticated_user_returns_recent_notifications_and_unread_count(self):
        # Create notifications for student
        for i in range(25):
            Notification.objects.create(
                recipient=self.student_user,
                event_type=Notification.EventType.BOOKING_CONFIRMED,
                title=f'Notification {i}',
                message=f'Message {i}',
                is_read=(i >= 5),  # 5 unread, 20 read
            )

        # Create notification for teacher (should not leak into student context)
        Notification.objects.create(
            recipient=self.teacher_user,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Teacher Notification',
            message='Teacher Message',
            is_read=False,
        )

        request = self.factory.get('/dashboard/student/')
        request.user = self.student_user

        context = notification_context(request)

        # Capped at 20 most recent
        notifications = list(context['recent_notifications'])
        self.assertEqual(len(notifications), 20)
        # Verify ordering is newest first (-created_at)
        self.assertEqual(notifications[0].title, 'Notification 24')
        # All notifications belong to the recipient
        for n in notifications:
            self.assertEqual(n.recipient, self.student_user)

        # Unread count reflects total unread for student (5)
        self.assertEqual(context['unread_notifications_count'], 5)
