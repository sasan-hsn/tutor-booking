from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from accounts.tests.base import RoleTestCase
from booking.models import Booking


class TeacherDashboardLessonRequestsBadgeTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile

    def test_lesson_requests_badge_zero_count_renders_persistent_hidden_badge(self):
        """When lesson_requests_count is 0, #lessonRequestsBadge is rendered with d-none and aria-live="polite"."""
        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['lesson_requests_count'], 0)

        expected_badge = (
            '<span class="badge-count d-none" id="lessonRequestsBadge" '
            'aria-live="polite">0</span>'
        )
        self.assertContains(response, expected_badge, html=False)
        self.assertContains(response, 'id="openLessonRequestsBtn"')

    def test_lesson_requests_badge_positive_count_renders_visible_badge(self):
        """When lesson_requests_count > 0, #lessonRequestsBadge is rendered without d-none and displays count."""
        now = timezone.now()
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, minutes=50),
            lesson_type=Booking.LessonType.REGULAR,
            duration_minutes=50,
            status=Booking.Status.PENDING,
        )
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now + timedelta(days=3),
            end_at=now + timedelta(days=3, minutes=50),
            lesson_type=Booking.LessonType.REGULAR,
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
            cancellation_requested=True,
        )

        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['lesson_requests_count'], 2)

        expected_badge = (
            '<span class="badge-count" id="lessonRequestsBadge" '
            'aria-live="polite">2</span>'
        )
        self.assertContains(response, expected_badge, html=False)
        self.assertNotContains(response, 'badge-count d-none')
