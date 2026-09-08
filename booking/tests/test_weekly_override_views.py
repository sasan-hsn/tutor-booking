from datetime import date, time, timedelta
from django.urls import reverse
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from booking.models import WeeklyOverride, RegularAvailability


class WeeklyOverrideViewTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile
        self.today = timezone.localdate()
        self.this_monday = self.today - timedelta(days=self.today.weekday())
        self.future_date = self.this_monday + timedelta(days=2)

    # --- teacher_weekly_override (GET) ---

    def test_default_week_is_current_week(self):
        response = self.teacher_client.get(reverse('booking:teacher_weekly_override'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['week_start'], self.this_monday.isoformat())

    def test_week_start_param_navigates_to_requested_week(self):
        next_monday = self.this_monday + timedelta(days=7)
        response = self.teacher_client.get(
            reverse('booking:teacher_weekly_override'),
            {'week_start': next_monday.isoformat()},
        )
        data = response.json()
        self.assertEqual(data['week_start'], next_monday.isoformat())
        self.assertEqual(data['prev_week_start'], self.this_monday.isoformat())

    def test_invalid_week_start_falls_back_to_current_week(self):
        response = self.teacher_client.get(
            reverse('booking:teacher_weekly_override'),
            {'week_start': 'not-a-date'},
        )
        data = response.json()
        self.assertEqual(data['week_start'], self.this_monday.isoformat())

    def test_past_days_marked_is_past(self):
        response = self.teacher_client.get(reverse('booking:teacher_weekly_override'))
        data = response.json()
        for day_str, day_data in data['days'].items():
            expected = date.fromisoformat(day_str) < self.today
            self.assertEqual(day_data['is_past'], expected)

    def test_regular_availability_appears_in_correct_day(self):
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=self.future_date.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        response = self.teacher_client.get(reverse('booking:teacher_weekly_override'))
        data = response.json()
        day_data = data['days'][self.future_date.isoformat()]
        self.assertEqual(day_data['regular_ranges'], [{'start': '09:00', 'end': '17:00'}])

    def test_active_override_appears_in_correct_day(self):
        override = WeeklyOverride.objects.create(
            teacher=self.teacher,
            date=self.future_date,
            start_time=time(10, 0),
            end_time=time(12, 0),
            is_available=True,
        )
        response = self.teacher_client.get(reverse('booking:teacher_weekly_override'))
        data = response.json()
        day_data = data['days'][self.future_date.isoformat()]
        self.assertEqual(len(day_data['override_ranges']), 1)
        self.assertEqual(day_data['override_ranges'][0]['id'], override.id)

    # --- teacher_weekly_override_add ---

    def test_add_valid_override_creates_it(self):
        response = self.teacher_client.post(
            reverse('booking:teacher_weekly_override_add'),
            {
                'date': self.future_date.isoformat(),
                'start_time': '10:00',
                'end_time': '12:00',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WeeklyOverride.objects.count(), 1)

    def test_add_invalid_override_returns_400(self):
        response = self.teacher_client.post(
            reverse('booking:teacher_weekly_override_add'),
            {
                'date': self.future_date.isoformat(),
                'start_time': '14:00',
                'end_time': '10:00',  # end before start
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WeeklyOverride.objects.count(), 0)

    # --- teacher_weekly_override_delete ---

    def test_delete_own_override_succeeds(self):
        override = WeeklyOverride.objects.create(
            teacher=self.teacher, date=self.future_date,
            start_time=time(10, 0), end_time=time(12, 0), is_available=True,
        )
        response = self.teacher_client.post(
            reverse('booking:teacher_weekly_override_delete', kwargs={'override_id': override.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WeeklyOverride.objects.filter(id=override.id).exists())

    def test_delete_another_teachers_override_returns_404(self):
        from accounts.models import User
        other_teacher = User.objects.create_user(
            username='other_t3', password='x', role=User.Role.TEACHER
        )
        override = WeeklyOverride.objects.create(
            teacher=other_teacher.teacher_profile, date=self.future_date,
            start_time=time(10, 0), end_time=time(12, 0), is_available=True,
        )
        response = self.teacher_client.post(
            reverse('booking:teacher_weekly_override_delete', kwargs={'override_id': override.id})
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(WeeklyOverride.objects.filter(id=override.id).exists())