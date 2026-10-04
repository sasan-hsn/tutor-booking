from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from accounts.tests.base import RoleTestCase
from booking.models import Booking


class DashboardDurationBadgesTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile
        now = timezone.now()

        # Future upcoming lessons: 1 trial (25m), 1 regular (50m)
        self.upcoming_trial = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, minutes=25),
            lesson_type=Booking.LessonType.TRIAL,
            duration_minutes=25,
            status=Booking.Status.CONFIRMED,
        )
        self.upcoming_regular = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now + timedelta(days=3),
            end_at=now + timedelta(days=3, minutes=50),
            lesson_type=Booking.LessonType.REGULAR,
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
        )

        # Past lessons: 1 trial (25m), 1 regular (50m)
        self.past_trial = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now - timedelta(days=5),
            end_at=now - timedelta(days=5) + timedelta(minutes=25),
            lesson_type=Booking.LessonType.TRIAL,
            duration_minutes=25,
            status=Booking.Status.COMPLETED,
        )
        self.past_regular = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now - timedelta(days=3),
            end_at=now - timedelta(days=3) + timedelta(minutes=50),
            lesson_type=Booking.LessonType.REGULAR,
            duration_minutes=50,
            status=Booking.Status.COMPLETED,
        )

        # Pending lesson request
        self.pending_request = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now + timedelta(days=4),
            end_at=now + timedelta(days=4, minutes=25),
            lesson_type=Booking.LessonType.TRIAL,
            duration_minutes=25,
            status=Booking.Status.PENDING,
        )

    def test_student_dashboard_renders_upcoming_duration_badges(self):
        response = self.student_client.get(reverse('booking:student_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span class="badge-duration">25 min</span>')
        self.assertContains(response, '<span class="badge-duration">50 min</span>')
        self.assertContains(response, 'class="lesson-card-time-row"')

    def test_student_dashboard_renders_past_duration_badges(self):
        response = self.student_client.get(reverse('booking:student_dashboard'))
        self.assertEqual(response.status_code, 200)
        # Past section contains both duration badges
        self.assertContains(response, '<span class="badge-duration">25 min</span>')
        self.assertContains(response, '<span class="badge-duration">50 min</span>')

    def test_teacher_dashboard_renders_upcoming_duration_badges(self):
        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span class="badge-duration">25 min</span>')
        self.assertContains(response, '<span class="badge-duration">50 min</span>')
        self.assertContains(response, 'class="lesson-card-time-row"')

    def test_teacher_dashboard_renders_past_duration_badges(self):
        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span class="badge-duration">25 min</span>')
        self.assertContains(response, '<span class="badge-duration">50 min</span>')

    def test_teacher_lesson_requests_modal_renders_duration_badge(self):
        response = self.teacher_client.get(reverse('booking:teacher_lesson_requests'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span class="badge-duration">25 min</span>')

    def test_teacher_lesson_detail_modal_renders_duration_row(self):
        response = self.teacher_client.get(
            reverse('booking:lesson_detail', kwargs={'booking_id': self.upcoming_regular.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span class="lesson-detail-label">Duration</span>')
        self.assertContains(response, '<span class="badge-duration">50 min</span>')

    def test_student_lesson_detail_modal_renders_duration_row(self):
        response = self.student_client.get(
            reverse('booking:lesson_detail_student', kwargs={'booking_id': self.upcoming_trial.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span class="lesson-detail-label">Duration</span>')
        self.assertContains(response, '<span class="badge-duration">25 min</span>')

    def test_ajax_pagination_upcoming_renders_duration_badge(self):
        response = self.student_client.get(
            reverse('booking:student_dashboard'),
            {'section': 'upcoming', 'page': 1},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span class="badge-duration">25 min</span>')
        self.assertContains(response, '<span class="badge-duration">50 min</span>')

    def test_ajax_pagination_past_renders_duration_badge(self):
        response = self.teacher_client.get(
            reverse('booking:teacher_dashboard'),
            {'section': 'past', 'page': 1},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span class="badge-duration">25 min</span>')
        self.assertContains(response, '<span class="badge-duration">50 min</span>')
