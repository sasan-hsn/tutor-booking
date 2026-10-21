from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from notifications.models import Notification

User = get_user_model()


class DashboardNotificationUITests(RoleTestCase):
    def test_dashboard_renders_bell_badge_dropdown_and_empty_state_when_no_notifications(self):
        url = reverse('booking:student_dashboard')
        response = self.student_client.get(url)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Bell button & badge
        self.assertIn('id="notificationDropdownBtn"', content)
        self.assertIn('id="notificationBadge"', content)
        self.assertIn('d-none', content)  # 0 unread -> badge is hidden with d-none
        self.assertIn('id="notificationBadgeCount"', content)
        self.assertIn('0', content)

        # Dropdown header
        self.assertIn('id="notificationDropdownMenu"', content)
        self.assertIn('id="notificationDropdownCountBadge"', content)
        self.assertIn('0 unread', content)
        self.assertIn('id="markAllReadBtn"', content)
        self.assertIn('disabled', content)

        # Empty state
        self.assertIn('id="noNotificationsMessage"', content)
        self.assertIn('No notifications yet', content)

        # Toast container & script
        self.assertIn('id="notificationToastContainer"', content)
        self.assertIn('notifications.js', content)

    def test_dashboard_renders_unread_badge_and_notification_items(self):
        notif1 = Notification.objects.create(
            recipient=self.student_user,
            event_type=Notification.EventType.BOOKING_CONFIRMED,
            title='Lesson Confirmed',
            message='Your lesson with Mary is confirmed.',
            action_url='/dashboard/student/',
            is_read=False,
            level=Notification.Level.SUCCESS,
        )
        notif2 = Notification.objects.create(
            recipient=self.student_user,
            event_type=Notification.EventType.BOOKING_CANCELLED,
            title='Lesson Cancelled',
            message='Your lesson was cancelled.',
            action_url='/dashboard/student/calendar/',
            is_read=True,
            level=Notification.Level.WARNING,
        )

        url = reverse('booking:student_dashboard')
        response = self.student_client.get(url)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Badge shows unread count (1) and is not hidden with d-none
        self.assertIn('id="notificationBadge"', content)
        self.assertIn('>1</span>', content)
        self.assertIn('1 unread', content)

        # Items rendered
        self.assertIn(f'data-id="{notif1.id}"', content)
        self.assertIn('data-action-url="/dashboard/student/"', content)
        self.assertIn('notification-unread', content)
        self.assertIn('notification-dot', content)
        self.assertIn('Lesson Confirmed', content)
        self.assertIn('Lesson Cancelled', content)
        self.assertNotIn('No notifications yet', content)

    def test_teacher_dashboard_also_renders_notification_components(self):
        url = reverse('booking:teacher_dashboard')
        response = self.teacher_client.get(url)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        self.assertIn('id="notificationDropdownBtn"', content)
        self.assertIn('id="notificationBadge"', content)
        self.assertIn('id="notificationToastContainer"', content)
        self.assertIn('notifications.js', content)
