from datetime import date, time, timedelta
from zoneinfo import ZoneInfo
from django.urls import reverse
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from booking.models import Booking, RegularAvailability, WeeklyOverride


class StudentVerificationBannerTests(RoleTestCase):
    """Test persistent verification banner display on student views."""

    def test_unverified_student_sees_banner_on_views(self):
        self.student_user.is_email_verified = False
        self.student_user.save()

        urls_to_test = [
            reverse('booking:student_dashboard'),
            reverse('booking:student_booking'),
            reverse('booking:student_calendar'),
            reverse('accounts:profile_settings'),
        ]

        for url in urls_to_test:
            response = self.student_client.get(url)
            self.assertEqual(response.status_code, 200, f"Expected 200 for {url}")
            self.assertContains(
                response,
                'id="studentVerificationBanner"',
                msg_prefix=f"Banner missing on {url}"
            )
            self.assertContains(
                response,
                'Please verify your email address to book lessons.',
                msg_prefix=f"Warning copy missing on {url}"
            )
            self.assertContains(
                response,
                'Resend verification email',
                msg_prefix=f"Resend action missing on {url}"
            )

    def test_verified_student_does_not_see_banner(self):
        self.student_user.is_email_verified = True
        self.student_user.save()

        urls_to_test = [
            reverse('booking:student_dashboard'),
            reverse('booking:student_booking'),
            reverse('booking:student_calendar'),
            reverse('accounts:profile_settings'),
        ]

        for url in urls_to_test:
            response = self.student_client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(
                response,
                'id="studentVerificationBanner"',
                msg_prefix=f"Banner unexpectedly shown on {url} for verified student"
            )

    def test_teacher_does_not_see_student_banner(self):
        # Even if unverified, teachers should not see the student booking warning banner
        self.teacher_user.is_email_verified = False
        self.teacher_user.save()

        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="studentVerificationBanner"')


class StudentBookingSoftGateTests(RoleTestCase):
    """Test student booking soft-gate and calendar exploration."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.lesson_price = 30
        cls.teacher.lesson_duration_minutes = 60
        cls.teacher.save()

    def setUp(self):
        super().setUp()
        self.tz = ZoneInfo(self.teacher_user.timezone)
        self.future_day = timezone.localdate() + timedelta(days=2)
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=self.future_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        self.valid_start = timezone.datetime.combine(
            self.future_day, time(10, 0), tzinfo=self.tz
        )

    def test_unverified_student_can_explore_calendar_and_view_slots(self):
        self.student_user.is_email_verified = False
        self.student_user.save()

        # Page render has modal and day-picker
        response = self.student_client.get(reverse('booking:student_booking'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="dayPicker"')
        self.assertContains(response, 'id="confirmBookingModal"')
        self.assertContains(response, 'id="modalVerificationAlert"')
        self.assertContains(response, 'slot-pill slot-open')

        # AJAX week navigation endpoint also returns slots
        week_start = self.future_day - timedelta(days=self.future_day.weekday())
        ajax_response = self.student_client.get(
            reverse('booking:student_booking_week_ajax'),
            {'week_start': week_start.isoformat()}
        )
        self.assertEqual(ajax_response.status_code, 200)
        self.assertIn('slot-pill slot-open', ajax_response.json()['html'])

    def test_unverified_student_submitting_booking_returns_403(self):
        self.student_user.is_email_verified = False
        self.student_user.save()

        response = self.student_client.post(
            reverse('booking:book_slot'),
            {'start_at': self.valid_start.isoformat()}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {'error': 'email_unverified'})
        self.assertEqual(Booking.objects.count(), 0)

    def test_verified_student_can_book_slot(self):
        self.student_user.is_email_verified = True
        self.student_user.save()

        response = self.student_client.post(
            reverse('booking:book_slot'),
            {'start_at': self.valid_start.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Booking.objects.count(), 1)
        booking = Booking.objects.first()
        self.assertEqual(booking.student, self.student_user)


class TeacherAvailabilityPublishingGateTests(RoleTestCase):
    """Test teacher availability creation/modification gate and day-picker exclusion."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.lesson_price = 25
        cls.teacher.lesson_duration_minutes = 60
        cls.teacher.save()

    def setUp(self):
        super().setUp()
        self.future_day = timezone.localdate() + timedelta(days=2)

    def test_unverified_teacher_cannot_add_regular_availability(self):
        self.teacher_user.is_email_verified = False
        self.teacher_user.save()

        response = self.teacher_client.post(
            reverse('booking:teacher_regular_schedule_add'),
            {
                'day_of_week': 1,
                'start_time': '09:00',
                'end_time': '12:00',
            }
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn('You must verify your email address', response.json().get('error', ''))
        self.assertEqual(RegularAvailability.objects.count(), 0)

    def test_verified_teacher_can_add_regular_availability(self):
        self.teacher_user.is_email_verified = True
        self.teacher_user.save()

        response = self.teacher_client.post(
            reverse('booking:teacher_regular_schedule_add'),
            {
                'day_of_week': 1,
                'start_time': '09:00',
                'end_time': '12:00',
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RegularAvailability.objects.count(), 1)

    def test_unverified_teacher_slots_excluded_from_public_day_picker(self):
        # Create availability while verified
        self.teacher_user.is_email_verified = True
        self.teacher_user.save()

        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=self.future_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
        )

        # Confirm slots appear while verified
        response = self.student_client.get(reverse('booking:student_booking'))
        self.assertContains(response, 'slot-pill slot-open')

        # Now mark teacher as unverified
        self.teacher_user.is_email_verified = False
        self.teacher_user.save()

        # Day picker renders no slots
        response = self.student_client.get(reverse('booking:student_booking'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'slot-pill slot-open')
        self.assertContains(response, 'day-column-no-slots')

        # AJAX endpoint also renders no slots
        week_start = self.future_day - timedelta(days=self.future_day.weekday())
        ajax_response = self.student_client.get(
            reverse('booking:student_booking_week_ajax'),
            {'week_start': week_start.isoformat()}
        )
        self.assertEqual(ajax_response.status_code, 200)
        self.assertNotIn('slot-pill slot-open', ajax_response.json()['html'])
        self.assertIn('day-column-no-slots', ajax_response.json()['html'])

    def test_unverified_teacher_can_configure_bio_video_and_pricing(self):
        self.teacher_user.is_email_verified = False
        self.teacher_user.save()

        # Configure portfolio (bio, philosophy, video)
        portfolio_url = reverse('portfolio:teacher_settings_portfolio')
        get_res = self.teacher_client.get(portfolio_url)
        self.assertEqual(get_res.status_code, 200)

        post_res = self.teacher_client.post(portfolio_url, {
            'headline': 'Experienced ESL Instructor',
            'bio': 'I have taught English for over 10 years.',
            'teaching_philosophy': 'Communicative and student-centered.',
            'intro_video_url': 'https://youtube.com/watch?v=sample123',
        })
        self.assertEqual(post_res.status_code, 302)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.headline, 'Experienced ESL Instructor')
        self.assertEqual(self.teacher.bio, 'I have taught English for over 10 years.')

        # Configure booking (pricing, meeting link)
        booking_settings_url = reverse('portfolio:teacher_settings_booking')
        get_booking_res = self.teacher_client.get(booking_settings_url)
        self.assertEqual(get_booking_res.status_code, 200)

        post_booking_res = self.teacher_client.post(booking_settings_url, {
            'meeting_link': 'https://meet.google.com/abc-defg-hij',
            'lesson_price': '45.00',
            'lesson_duration_minutes': 50,
            'trial_price': '15.00',
            'trial_duration_minutes': 25,
        })
        self.assertEqual(post_booking_res.status_code, 302)
        self.teacher.refresh_from_db()
        self.assertEqual(float(self.teacher.lesson_price), 45.00)
        self.assertEqual(self.teacher.meeting_link, 'https://meet.google.com/abc-defg-hij')
