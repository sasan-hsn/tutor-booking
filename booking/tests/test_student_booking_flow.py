from datetime import date, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.urls import reverse
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from booking.models import Booking, RegularAvailability
from portfolio.models import TeacherProfile


class StudentBookingFlowTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.lesson_price = Decimal('40.00')
        cls.teacher.lesson_price_25 = Decimal('22.00')
        cls.teacher.offers_trial = True
        cls.teacher.trial_price = Decimal('10.00')
        cls.teacher.trial_duration_minutes = 25
        cls.teacher.lesson_duration_minutes = 50
        cls.teacher.save()

    def setUp(self):
        super().setUp()
        self.tz = ZoneInfo(self.teacher_user.timezone)
        self.future_day = timezone.localdate() + timedelta(days=2)
        # 10:00 to 10:30 window: fits a 25m lesson (10:00-10:25), but NOT a 50m lesson (10:00-10:50)
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=self.future_day.weekday(),
            start_time=time(10, 0),
            end_time=time(10, 30),
        )
        # 14:00 to 16:00 window: fits both 25m and 50m lessons
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=self.future_day.weekday(),
            start_time=time(14, 0),
            end_time=time(16, 0),
        )

    def test_first_time_student_booking_page_renders_three_options_with_trial_preselected(self):
        response = self.student_client.get(reverse('booking:student_booking'))
        self.assertEqual(response.status_code, 200)

        # Context assertions
        lesson_options = response.context['lesson_options']
        self.assertEqual(len(lesson_options), 3)
        self.assertEqual(lesson_options[0]['key'], 'trial')
        self.assertTrue(lesson_options[0]['is_selected'])
        self.assertEqual(lesson_options[1]['key'], 'regular_25')
        self.assertFalse(lesson_options[1]['is_selected'])
        self.assertEqual(lesson_options[2]['key'], 'regular_50')
        self.assertFalse(lesson_options[2]['is_selected'])

        # HTML assertions
        content = response.content.decode()
        self.assertIn('data-lesson-option="trial"', content)
        self.assertIn('data-lesson-option="regular_25"', content)
        self.assertIn('data-lesson-option="regular_50"', content)
        self.assertIn('Trial Lesson', content)
        self.assertIn('Regular Lesson (25m)', content)
        self.assertIn('Regular Lesson (50m)', content)
        self.assertIn('$10.00', content)
        self.assertIn('$22.00', content)
        self.assertIn('$40.00', content)

        # Confirmation modal markup contains required elements
        self.assertIn('id="confirmBookingModal"', content)
        self.assertIn('id="modalTeacherAvatar"', content)
        self.assertIn('id="modalTeacherName"', content)
        self.assertIn('id="modalLessonTypeBadge"', content)
        self.assertIn('id="modalLessonDuration"', content)
        self.assertIn('id="modalLessonPrice"', content)
        self.assertIn('id="modalLessonDate"', content)
        self.assertIn('id="modalLessonTime"', content)

    def test_returning_student_booking_page_hides_trial_and_preselects_regular_50(self):
        # Create prior completed booking
        now = timezone.now()
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now - timedelta(days=3),
            end_at=now - timedelta(days=3) + timedelta(minutes=50),
            lesson_type=Booking.LessonType.REGULAR,
            price=self.teacher.lesson_price,
            status=Booking.Status.COMPLETED,
        )

        response = self.student_client.get(reverse('booking:student_booking'))
        self.assertEqual(response.status_code, 200)

        lesson_options = response.context['lesson_options']
        self.assertEqual(len(lesson_options), 2)
        keys = [opt['key'] for opt in lesson_options]
        self.assertNotIn('trial', keys)
        self.assertEqual(keys, ['regular_25', 'regular_50'])

        reg_50 = next(opt for opt in lesson_options if opt['key'] == 'regular_50')
        self.assertTrue(reg_50['is_selected'])

        content = response.content.decode()
        self.assertNotIn('data-lesson-option="trial"', content)
        self.assertIn('data-lesson-option="regular_25"', content)
        self.assertIn('data-lesson-option="regular_50"', content)

    def test_teacher_not_offering_trial_hides_trial_for_first_time_student(self):
        self.teacher.offers_trial = False
        self.teacher.save()

        response = self.student_client.get(reverse('booking:student_booking'))
        self.assertEqual(response.status_code, 200)

        lesson_options = response.context['lesson_options']
        self.assertEqual(len(lesson_options), 2)
        self.assertNotIn('trial', [opt['key'] for opt in lesson_options])

    def test_ajax_week_reload_with_25m_duration_includes_30m_window_slot(self):
        # 10:00 start fits 25m, but not 50m
        week_start = self.future_day - timedelta(days=self.future_day.weekday())
        response = self.student_client.get(
            reverse('booking:student_booking_week_ajax'),
            {'week_start': week_start.isoformat(), 'duration': 25},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        html = data['html']

        # 10:00 slot should be present for 25m
        self.assertIn('10:00', html)
        self.assertEqual(data.get('duration_minutes'), 25)

    def test_ajax_week_reload_with_50m_duration_excludes_30m_window_slot(self):
        # 10:00 start does NOT fit 50m (window ends at 10:30)
        week_start = self.future_day - timedelta(days=self.future_day.weekday())
        response = self.student_client.get(
            reverse('booking:student_booking_week_ajax'),
            {'week_start': week_start.isoformat(), 'duration': 50},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        html = data['html']

        # 10:00 slot cannot accommodate 50m inside the 10:00-10:30 window
        self.assertNotIn('10:00', html)
        # But 14:00 slot fits 50m
        self.assertIn('14:00', html)
        self.assertEqual(data.get('duration_minutes'), 50)

    def test_ajax_week_reload_with_lesson_option_param(self):
        week_start = self.future_day - timedelta(days=self.future_day.weekday())
        response = self.student_client.get(
            reverse('booking:student_booking_week_ajax'),
            {'week_start': week_start.isoformat(), 'lesson_option': 'regular_50'},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('duration_minutes'), 50)
