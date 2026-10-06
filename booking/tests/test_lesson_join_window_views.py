from datetime import timedelta
from unittest.mock import patch
from django.urls import reverse
from django.utils import timezone
from accounts.tests.base import RoleTestCase
from booking.models import Booking


class TeacherDashboardJoinWindowTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.meeting_link = 'https://meet.google.com/abc-defg-hij'
        self.teacher.save()

        self.now = timezone.now()
        # Booking starting in 5 minutes (within 10-minute pre-start join window)
        self.joinable_booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=self.now + timedelta(minutes=5),
            end_at=self.now + timedelta(minutes=55),
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
        )

        # Booking starting in 2 days (outside join window, no collision with test offsets)
        self.future_booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=self.now + timedelta(days=2),
            end_at=self.now + timedelta(days=2, minutes=50),
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
        )

    def test_teacher_dashboard_renders_join_button_when_joinable_with_meeting_link(self):
        """Card renders 'Join Lesson' with target='_blank' and rel='noopener noreferrer' when joinable."""
        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)

        # Join button must be present for joinable booking
        self.assertContains(response, 'Join Lesson')
        self.assertContains(response, 'https://meet.google.com/abc-defg-hij')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, 'rel="noopener noreferrer"')

        # Check data attributes on card
        self.assertContains(response, f'data-start-at="{self.joinable_booking.start_at.isoformat()}"')
        self.assertContains(response, f'data-end-at="{self.joinable_booking.end_at.isoformat()}"')
        self.assertContains(response, 'data-meeting-link="https://meet.google.com/abc-defg-hij"')
        self.assertContains(response, 'data-status="confirmed"')
        self.assertContains(response, 'data-is-teacher="true"')

    def test_teacher_dashboard_shows_set_meeting_link_warning_when_link_missing(self):
        """When teacher has no meeting link, teacher card renders warning link to booking settings."""
        self.teacher.meeting_link = ''
        self.teacher.save()

        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)

        self.assertNotContains(response, 'Join Lesson')
        self.assertContains(response, '⚠️ Set Meeting Link')
        expected_url = f"{reverse('portfolio:teacher_settings_booking')}?next={reverse('booking:teacher_dashboard')}"
        self.assertContains(response, expected_url)

    def test_teacher_dashboard_omits_join_button_outside_join_window(self):
        """When confirmed booking is outside the 10-minute window, no join button is rendered."""
        # Shift time so both bookings are in the distant future
        with patch('django.utils.timezone.now', return_value=self.now - timedelta(hours=2)):
            response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'Join Lesson')
            self.assertNotContains(response, '⚠️ Set Meeting Link')

    def test_teacher_dashboard_renders_both_live_and_join_during_active_lesson(self):
        """During active lesson interval, both 'Live Now' badge and 'Join Lesson' button render."""
        # Set time to 15 minutes after start_at
        mid_lesson = self.joinable_booking.start_at + timedelta(minutes=15)
        with patch('django.utils.timezone.now', return_value=mid_lesson):
            response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Live Now')
            self.assertContains(response, 'Join Lesson')

    def test_teacher_dashboard_renders_join_during_post_end_grace_period(self):
        """During post-end grace period (end_at + 5m), teacher sees 'Join Lesson'."""
        post_end_time = self.joinable_booking.end_at + timedelta(minutes=5)
        with patch('django.utils.timezone.now', return_value=post_end_time):
            response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Join Lesson')

    def test_teacher_dashboard_omits_join_after_post_end_window_closes(self):
        """After post-end window closes (end_at + 11m), 'Join Lesson' is not rendered."""
        past_window_time = self.joinable_booking.end_at + timedelta(minutes=11)
        with patch('django.utils.timezone.now', return_value=past_window_time):
            response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'Join Lesson')

    def test_teacher_dashboard_omits_join_for_pending_booking(self):
        """Pending booking never renders a join button."""
        self.joinable_booking.status = Booking.Status.PENDING
        self.joinable_booking.save()

        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Join Lesson')

    def test_teacher_dashboard_ajax_upcoming_renders_join_metadata(self):
        """AJAX pagination for upcoming cards includes join button and dataset attributes."""
        response = self.teacher_client.get(
            reverse('booking:teacher_dashboard'),
            {'section': 'upcoming', 'page': 1},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Join Lesson')
        self.assertContains(response, 'data-is-teacher="true"')
        self.assertContains(response, 'data-meeting-link="https://meet.google.com/abc-defg-hij"')


class StudentDashboardJoinWindowTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.meeting_link = 'https://meet.google.com/abc-defg-hij'
        self.teacher.save()

        self.now = timezone.now()
        self.joinable_booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=self.now + timedelta(minutes=5),
            end_at=self.now + timedelta(minutes=55),
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
        )

    def test_student_dashboard_renders_join_button_when_joinable(self):
        """Student card renders 'Join Lesson' button with data attributes when joinable."""
        response = self.student_client.get(reverse('booking:student_dashboard'))
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, 'Join Lesson')
        self.assertContains(response, 'https://meet.google.com/abc-defg-hij')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, 'rel="noopener noreferrer"')
        self.assertContains(response, 'data-is-teacher="false"')

    def test_student_dashboard_omits_button_when_teacher_has_no_meeting_link(self):
        """Student card renders NO button when teacher has no meeting link (clean card layout)."""
        self.teacher.meeting_link = ''
        self.teacher.save()

        response = self.student_client.get(reverse('booking:student_dashboard'))
        self.assertEqual(response.status_code, 200)

        self.assertNotContains(response, 'Join Lesson')
        self.assertNotContains(response, '⚠️ Set Meeting Link')

    def test_student_dashboard_renders_join_during_post_end_grace_period(self):
        """During post-end grace period (end_at + 5m), student sees 'Join Lesson'."""
        post_end_time = self.joinable_booking.end_at + timedelta(minutes=5)
        with patch('django.utils.timezone.now', return_value=post_end_time):
            response = self.student_client.get(reverse('booking:student_dashboard'))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Join Lesson')

    def test_student_dashboard_omits_join_after_post_end_window_closes(self):
        """After post-end window closes (end_at + 11m), student card has no 'Join Lesson'."""
        past_window_time = self.joinable_booking.end_at + timedelta(minutes=11)
        with patch('django.utils.timezone.now', return_value=past_window_time):
            response = self.student_client.get(reverse('booking:student_dashboard'))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'Join Lesson')

    def test_student_dashboard_omits_join_button_outside_window(self):
        """Student card renders no join button when outside the join window."""
        with patch('django.utils.timezone.now', return_value=self.now - timedelta(hours=2)):
            response = self.student_client.get(reverse('booking:student_dashboard'))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'Join Lesson')


class TeacherLessonDetailModalTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.meeting_link = 'https://meet.google.com/abc-defg-hij'
        self.teacher.save()

        self.now = timezone.now()
        self.booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=self.now + timedelta(minutes=5),
            end_at=self.now + timedelta(minutes=55),
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
        )

    def test_modal_renders_join_button_when_joinable(self):
        """Modal displays full-width 'Join Lesson' button when joinable with link."""
        url = reverse('booking:lesson_detail', kwargs={'booking_id': self.booking.id})
        response = self.teacher_client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, 'Join Lesson')
        self.assertContains(response, 'https://meet.google.com/abc-defg-hij')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, 'rel="noopener noreferrer"')
        self.assertNotContains(response, 'Meeting link accessible 10 minutes before lesson')

    def test_modal_renders_set_meeting_link_warning_for_teacher(self):
        """Modal displays '⚠️ Set Meeting Link' warning when teacher has no meeting link."""
        self.teacher.meeting_link = ''
        self.teacher.save()

        url = reverse('booking:lesson_detail', kwargs={'booking_id': self.booking.id})
        response = self.teacher_client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertNotContains(response, 'Join Lesson')
        self.assertContains(response, '⚠️ Set Meeting Link')
        expected_url = f"{reverse('portfolio:teacher_settings_booking')}?next={reverse('booking:teacher_dashboard')}"
        self.assertContains(response, expected_url)

    def test_modal_displays_notice_outside_join_window(self):
        """Modal displays 'Meeting link accessible 10 minutes before lesson' when outside window."""
        with patch('django.utils.timezone.now', return_value=self.now - timedelta(hours=2)):
            url = reverse('booking:lesson_detail', kwargs={'booking_id': self.booking.id})
            response = self.teacher_client.get(url)
            self.assertEqual(response.status_code, 200)

            self.assertNotContains(response, 'Join Lesson')
            self.assertNotContains(response, '⚠️ Set Meeting Link')
            self.assertContains(response, 'Meeting link accessible 10 minutes before lesson')

    def test_modal_renders_join_during_post_end_grace_period(self):
        """Teacher modal renders 'Join Lesson' during post-end grace period (end_at + 5m)."""
        post_end_time = self.booking.end_at + timedelta(minutes=5)
        with patch('django.utils.timezone.now', return_value=post_end_time):
            url = reverse('booking:lesson_detail', kwargs={'booking_id': self.booking.id})
            response = self.teacher_client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Join Lesson')

    def test_modal_omits_join_after_post_end_window_closes(self):
        """Teacher modal omits 'Join Lesson' after window closes (end_at + 11m)."""
        past_window_time = self.booking.end_at + timedelta(minutes=11)
        with patch('django.utils.timezone.now', return_value=past_window_time):
            url = reverse('booking:lesson_detail', kwargs={'booking_id': self.booking.id})
            response = self.teacher_client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'Join Lesson')

    def test_modal_omits_join_access_for_cancelled_or_completed_booking(self):
        """Cancelled, completed, and expired bookings never render join buttons or notices."""
        for non_confirmed_status in [Booking.Status.CANCELLED, Booking.Status.COMPLETED, Booking.Status.EXPIRED]:
            self.booking.status = non_confirmed_status
            self.booking.save()

            url = reverse('booking:lesson_detail', kwargs={'booking_id': self.booking.id})
            response = self.teacher_client.get(url)
            self.assertEqual(response.status_code, 200)

            self.assertNotContains(response, 'Join Lesson')
            self.assertNotContains(response, '⚠️ Set Meeting Link')
            self.assertNotContains(response, 'Meeting link accessible 10 minutes before lesson')


class StudentLessonDetailModalTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.meeting_link = 'https://meet.google.com/abc-defg-hij'
        self.teacher.save()

        self.now = timezone.now()
        self.booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=self.now + timedelta(minutes=5),
            end_at=self.now + timedelta(minutes=55),
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
        )

    def test_student_modal_renders_join_button_when_joinable(self):
        """Student modal displays 'Join Lesson' button when joinable with link."""
        url = reverse('booking:lesson_detail_student', kwargs={'booking_id': self.booking.id})
        response = self.student_client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, 'Join Lesson')
        self.assertContains(response, 'https://meet.google.com/abc-defg-hij')

    def test_student_modal_renders_missing_link_notice(self):
        """Student modal displays explanatory notice when teacher has no meeting link."""
        self.teacher.meeting_link = ''
        self.teacher.save()

        url = reverse('booking:lesson_detail_student', kwargs={'booking_id': self.booking.id})
        response = self.student_client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertNotContains(response, 'Join Lesson')
        self.assertNotContains(response, '⚠️ Set Meeting Link')
        self.assertContains(response, "Teacher hasn't added a meeting link yet. It will appear here once configured.")

    def test_student_modal_renders_join_during_post_end_grace_period(self):
        """Student modal renders 'Join Lesson' during post-end grace period (end_at + 5m)."""
        post_end_time = self.booking.end_at + timedelta(minutes=5)
        with patch('django.utils.timezone.now', return_value=post_end_time):
            url = reverse('booking:lesson_detail_student', kwargs={'booking_id': self.booking.id})
            response = self.student_client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Join Lesson')

    def test_student_modal_omits_join_after_post_end_window_closes(self):
        """Student modal omits 'Join Lesson' after window closes (end_at + 11m)."""
        past_window_time = self.booking.end_at + timedelta(minutes=11)
        with patch('django.utils.timezone.now', return_value=past_window_time):
            url = reverse('booking:lesson_detail_student', kwargs={'booking_id': self.booking.id})
            response = self.student_client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'Join Lesson')

    def test_student_modal_omits_join_access_for_non_confirmed_statuses(self):
        """Non-confirmed statuses (CANCELLED, COMPLETED, EXPIRED) never render join actions."""
        for non_confirmed_status in [Booking.Status.CANCELLED, Booking.Status.COMPLETED, Booking.Status.EXPIRED]:
            self.booking.status = non_confirmed_status
            self.booking.save()

            url = reverse('booking:lesson_detail_student', kwargs={'booking_id': self.booking.id})
            response = self.student_client.get(url)
            self.assertEqual(response.status_code, 200)

            self.assertNotContains(response, 'Join Lesson')
            self.assertNotContains(response, '⚠️ Set Meeting Link')
            self.assertNotContains(response, 'Meeting link accessible 10 minutes before lesson')

    def test_student_modal_displays_notice_outside_join_window(self):
        """Student modal displays 'Meeting link accessible 10 minutes before lesson' outside window."""
        with patch('django.utils.timezone.now', return_value=self.now - timedelta(hours=2)):
            url = reverse('booking:lesson_detail_student', kwargs={'booking_id': self.booking.id})
            response = self.student_client.get(url)
            self.assertEqual(response.status_code, 200)

            self.assertNotContains(response, 'Join Lesson')
            self.assertContains(response, 'Meeting link accessible 10 minutes before lesson')


class TeacherSettingsBookingRedirectTests(RoleTestCase):
    def test_settings_preserves_next_parameter_and_redirects_on_save(self):
        """Teacher booking settings respects 'next' parameter upon saving form."""
        dashboard_url = reverse('booking:teacher_dashboard')
        get_url = f"{reverse('portfolio:teacher_settings_booking')}?next={dashboard_url}"
        get_response = self.teacher_client.get(get_url)
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, f'value="{dashboard_url}"')

        post_data = {
            'meeting_link': 'https://meet.google.com/new-meeting-link',
            'lesson_price': '25.00',
            'lesson_price_25': '15.00',
            'lesson_duration_minutes': 50,
            'offers_trial': True,
            'trial_price': '10.00',
            'trial_duration_minutes': 25,
            'next': dashboard_url,
        }
        post_response = self.teacher_client.post(
            reverse('portfolio:teacher_settings_booking'),
            post_data,
        )
        self.assertRedirects(post_response, dashboard_url)
        self.teacher_user.teacher_profile.refresh_from_db()
        self.assertEqual(
            self.teacher_user.teacher_profile.meeting_link,
            'https://meet.google.com/new-meeting-link',
        )

    def test_settings_unsafe_next_falls_back_to_settings_url(self):
        """Unsafe external 'next' url is rejected, falling back to default redirect."""
        post_data = {
            'meeting_link': 'https://meet.google.com/new-meeting-link',
            'lesson_price': '25.00',
            'lesson_price_25': '15.00',
            'lesson_duration_minutes': 50,
            'offers_trial': True,
            'trial_price': '10.00',
            'trial_duration_minutes': 25,
            'next': 'https://malicious-site.com',
        }
        post_response = self.teacher_client.post(
            reverse('portfolio:teacher_settings_booking'),
            post_data,
        )
        self.assertRedirects(post_response, reverse('portfolio:teacher_settings_booking'))
