from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from notifications.models import Notification

User = get_user_model()


class NotificationViewsTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.notif1 = Notification.objects.create(
            recipient=self.student_user,
            event_type=Notification.EventType.BOOKING_CONFIRMED,
            title='Confirmed 1',
            message='Message 1',
        )
        self.notif2 = Notification.objects.create(
            recipient=self.student_user,
            event_type=Notification.EventType.BOOKING_CONFIRMED,
            title='Confirmed 2',
            message='Message 2',
        )
        self.teacher_notif = Notification.objects.create(
            recipient=self.teacher_user,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            title='Teacher Notif',
            message='Teacher message',
        )

    def test_mark_notification_read_success(self):
        url = reverse('notifications:mark_read', kwargs={'pk': self.notif1.pk})
        response = self.student_client.post(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'unread_count': 1})

        self.notif1.refresh_from_db()
        self.assertTrue(self.notif1.is_read)
        self.assertIsNotNone(self.notif1.read_at)

    def test_mark_notification_read_already_read_is_idempotent(self):
        self.notif1.mark_as_read()
        url = reverse('notifications:mark_read', kwargs={'pk': self.notif1.pk})
        response = self.student_client.post(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'unread_count': 1})

    def test_mark_notification_read_other_user_returns_404(self):
        # Student cannot mark teacher's notification as read
        url = reverse('notifications:mark_read', kwargs={'pk': self.teacher_notif.pk})
        response = self.student_client.post(url)

        self.assertEqual(response.status_code, 404)
        self.teacher_notif.refresh_from_db()
        self.assertFalse(self.teacher_notif.is_read)

    def test_mark_notification_read_requires_login(self):
        url = reverse('notifications:mark_read', kwargs={'pk': self.notif1.pk})
        response = self.anon_client.post(url)

        self.assertEqual(response.status_code, 302)

    def test_mark_notification_read_requires_post(self):
        url = reverse('notifications:mark_read', kwargs={'pk': self.notif1.pk})
        response = self.student_client.get(url)

        self.assertEqual(response.status_code, 405)

    def test_mark_all_read_success(self):
        url = reverse('notifications:mark_all_read')
        response = self.student_client.post(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'unread_count': 0})

        self.notif1.refresh_from_db()
        self.notif2.refresh_from_db()
        self.assertTrue(self.notif1.is_read)
        self.assertTrue(self.notif2.is_read)
        self.assertIsNotNone(self.notif1.read_at)
        self.assertIsNotNone(self.notif2.read_at)

        # Teacher notification must remain unread
        self.teacher_notif.refresh_from_db()
        self.assertFalse(self.teacher_notif.is_read)

    def test_mark_all_read_requires_login(self):
        url = reverse('notifications:mark_all_read')
        response = self.anon_client.post(url)

        self.assertEqual(response.status_code, 302)

    def test_mark_all_read_requires_post(self):
        url = reverse('notifications:mark_all_read')
        response = self.student_client.get(url)

        self.assertEqual(response.status_code, 405)
