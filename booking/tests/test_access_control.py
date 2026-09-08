from django.urls import reverse
from accounts.tests.base import RoleTestCase


class TeacherOnlyViewsAccessTests(RoleTestCase):
    """Every @teacher_required view must reject students with 403 and
    redirect anonymous users to login."""

    def _teacher_only_urls(self):
        return [
            reverse('booking:teacher_dashboard'),
            reverse('booking:teacher_lesson_requests'),
            reverse('booking:respond_to_booking', kwargs={'booking_id': 1}),
            reverse('booking:student_management'),
            reverse('booking:teacher_regular_schedule'),
            reverse('booking:teacher_regular_schedule_add'),
            reverse('booking:teacher_regular_schedule_delete', kwargs={'availability_id': 1}),
            reverse('booking:teacher_weekly_override'),
            reverse('booking:teacher_weekly_override_add'),
            reverse('booking:teacher_weekly_override_delete', kwargs={'override_id': 1}),
            reverse('booking:teacher_calendar'),
            reverse('booking:teacher_calendar_ajax'),
            reverse('booking:lesson_detail', kwargs={'booking_id': 1}),
            reverse('booking:cancel_lesson', kwargs={'booking_id': 1}),
            reverse('booking:complete_lesson', kwargs={'booking_id': 1}),
            reverse('booking:mark_lesson_not_held', kwargs={'booking_id': 1}),
            reverse('booking:teacher_pending_reviews'),
            reverse('booking:approve_review', kwargs={'review_id': 1}),
            reverse('booking:reject_review', kwargs={'review_id': 1}),
            reverse('portfolio:teacher_settings_account'),
            reverse('portfolio:teacher_settings_portfolio'),
            reverse('portfolio:teacher_settings_booking'),
            reverse('portfolio:teacher_certificate_add'),
            reverse('portfolio:teacher_certificate_delete', kwargs={'certificate_id': 1}),
        ]

    def test_student_gets_403_on_teacher_only_urls(self):
        for url in self._teacher_only_urls():
            response = self.student_client.get(url)
            self.assertEqual(
                response.status_code, 403,
                f"Expected 403 for student on {url}, got {response.status_code}"
            )

    def test_anonymous_redirected_to_login_on_teacher_only_urls(self):
        for url in self._teacher_only_urls():
            response = self.anon_client.get(url)
            self.assertEqual(
                response.status_code, 302,
                f"Expected redirect for anonymous on {url}, got {response.status_code}"
            )
            self.assertIn('/accounts/login/', response.url)


class StudentOnlyViewsAccessTests(RoleTestCase):
    """Every @student_required view must reject teachers with 403 and
    redirect anonymous users to login."""

    def _student_only_urls(self):
        return [
            reverse('booking:student_dashboard'),
            reverse('booking:student_booking'),
            reverse('booking:student_booking_week_ajax'),
            reverse('booking:book_slot'),
            reverse('booking:student_calendar'),
            reverse('booking:student_calendar_ajax'),
            reverse('booking:lesson_detail_student', kwargs={'booking_id': 1}),
            reverse('booking:request_cancellation', kwargs={'booking_id': 1}),
            reverse('booking:submit_review', kwargs={'booking_id': 1}),
        ]

    def test_teacher_gets_403_on_student_only_urls(self):
        for url in self._student_only_urls():
            response = self.teacher_client.get(url)
            self.assertEqual(
                response.status_code, 403,
                f"Expected 403 for teacher on {url}, got {response.status_code}"
            )

    def test_anonymous_redirected_to_login_on_student_only_urls(self):
        for url in self._student_only_urls():
            response = self.anon_client.get(url)
            self.assertEqual(
                response.status_code, 302,
                f"Expected redirect for anonymous on {url}, got {response.status_code}"
            )
            self.assertIn('/accounts/login/', response.url)