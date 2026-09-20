import io
from datetime import timedelta
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from accounts.tests.base import RoleTestCase
from booking.models import Booking, RegularAvailability
from booking.services import expire_stale_bookings, get_lesson_type_and_price


class AutoExpireCoreTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile

    def _make_booking(self, status=Booking.Status.PENDING, hours_from_now=2, teacher=None, student=None):
        if teacher is None:
            teacher = self.teacher
        if student is None:
            student = self.student_user
        start = timezone.now() + timedelta(hours=hours_from_now)
        return Booking.objects.create(
            student=student,
            teacher=teacher,
            start_at=start,
            end_at=start + timedelta(hours=1),
            status=status,
        )

    # 1. Model Status Choice
    def test_expired_status_choice(self):
        self.assertEqual(Booking.Status.EXPIRED, 'expired')
        booking = self._make_booking(status=Booking.Status.EXPIRED, hours_from_now=-2)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.EXPIRED)

    # 2. Expiration Service
    def test_expire_stale_bookings_only_updates_past_pending(self):
        past_pending = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-2)
        future_pending = self._make_booking(status=Booking.Status.PENDING, hours_from_now=2)
        past_confirmed = self._make_booking(status=Booking.Status.CONFIRMED, hours_from_now=-5)
        past_completed = self._make_booking(status=Booking.Status.COMPLETED, hours_from_now=-8)
        past_cancelled = self._make_booking(status=Booking.Status.CANCELLED, hours_from_now=-11)

        count = expire_stale_bookings()
        self.assertEqual(count, 1)

        past_pending.refresh_from_db()
        future_pending.refresh_from_db()
        past_confirmed.refresh_from_db()
        past_completed.refresh_from_db()
        past_cancelled.refresh_from_db()

        self.assertEqual(past_pending.status, Booking.Status.EXPIRED)
        self.assertEqual(future_pending.status, Booking.Status.PENDING)
        self.assertEqual(past_confirmed.status, Booking.Status.CONFIRMED)
        self.assertEqual(past_completed.status, Booking.Status.COMPLETED)
        self.assertEqual(past_cancelled.status, Booking.Status.CANCELLED)

    def test_expire_stale_bookings_scoped_by_teacher(self):
        other_teacher_user = User.objects.create_user(
            username='other_teacher', password='x', role=User.Role.TEACHER
        )
        other_teacher = other_teacher_user.teacher_profile

        my_stale = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-2, teacher=self.teacher)
        other_stale = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-2, teacher=other_teacher)

        count = expire_stale_bookings(teacher=self.teacher)
        self.assertEqual(count, 1)

        my_stale.refresh_from_db()
        other_stale.refresh_from_db()

        self.assertEqual(my_stale.status, Booking.Status.EXPIRED)
        self.assertEqual(other_stale.status, Booking.Status.PENDING)

    def test_expire_stale_bookings_scoped_by_student(self):
        other_student = User.objects.create_user(
            username='other_student', password='x', role=User.Role.STUDENT
        )

        my_stale = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-2, student=self.student_user)
        other_stale = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-5, student=other_student)

        count = expire_stale_bookings(student=self.student_user)
        self.assertEqual(count, 1)

        my_stale.refresh_from_db()
        other_stale.refresh_from_db()

        self.assertEqual(my_stale.status, Booking.Status.EXPIRED)
        self.assertEqual(other_stale.status, Booking.Status.PENDING)

    # 3. Teacher Dashboard Badge
    def test_teacher_dashboard_excludes_expired_bookings(self):
        self._make_booking(status=Booking.Status.PENDING, hours_from_now=-2)
        self._make_booking(status=Booking.Status.PENDING, hours_from_now=3)

        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)
        # Should only count the 1 future pending booking, not the stale one
        self.assertEqual(response.context['lesson_requests_count'], 1)

    # 4. Teacher Lesson Requests Modal/List
    def test_teacher_lesson_requests_excludes_expired_bookings(self):
        stale_booking = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-2)
        future_booking = self._make_booking(status=Booking.Status.PENDING, hours_from_now=3)

        response = self.teacher_client.get(reverse('booking:teacher_lesson_requests'))
        self.assertEqual(response.status_code, 200)
        requests_list = list(response.context['lesson_requests'])
        self.assertIn(future_booking, requests_list)
        self.assertNotIn(stale_booking, requests_list)

        stale_booking.refresh_from_db()
        self.assertEqual(stale_booking.status, Booking.Status.EXPIRED)

    # 5. respond_to_booking Guardrails
    def test_respond_to_expired_booking_returns_400_and_transitions_status(self):
        stale_booking = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-1)

        response = self.teacher_client.post(
            reverse('booking:respond_to_booking', kwargs={'booking_id': stale_booking.id}),
            {'action': 'accept'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'This lesson request has expired.'})

        stale_booking.refresh_from_db()
        self.assertEqual(stale_booking.status, Booking.Status.EXPIRED)

    def test_respond_to_valid_future_booking_succeeds(self):
        future_booking = self._make_booking(status=Booking.Status.PENDING, hours_from_now=4)

        response = self.teacher_client.post(
            reverse('booking:respond_to_booking', kwargs={'booking_id': future_booking.id}),
            {'action': 'accept'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True})

        future_booking.refresh_from_db()
        self.assertEqual(future_booking.status, Booking.Status.CONFIRMED)

    def test_respond_to_already_expired_booking_returns_400(self):
        expired_booking = self._make_booking(status=Booking.Status.EXPIRED, hours_from_now=-4)

        response = self.teacher_client.post(
            reverse('booking:respond_to_booking', kwargs={'booking_id': expired_booking.id}),
            {'action': 'accept'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'This lesson request has expired.'})

    def test_student_dashboard_expires_stale_bookings(self):
        stale_booking = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-3)
        response = self.student_client.get(reverse('booking:student_dashboard'))
        self.assertEqual(response.status_code, 200)

        stale_booking.refresh_from_db()
        self.assertEqual(stale_booking.status, Booking.Status.EXPIRED)

    # 6. Trial Re-Eligibility
    def test_expired_trial_request_restores_trial_eligibility(self):
        self.teacher.offers_trial = True
        self.teacher.save()

        stale_trial = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-3)
        stale_trial.lesson_type = Booking.LessonType.TRIAL
        stale_trial.save()

        lesson_type, price, duration = get_lesson_type_and_price(self.teacher, self.student_user)
        self.assertEqual(lesson_type, Booking.LessonType.TRIAL)
        self.assertEqual(price, self.teacher.trial_price)
        self.assertEqual(duration, self.teacher.trial_duration_minutes)

        stale_trial.refresh_from_db()
        self.assertEqual(stale_trial.status, Booking.Status.EXPIRED)

    def test_student_can_book_new_trial_after_previous_trial_expired(self):
        self.teacher.offers_trial = True
        self.teacher.save()

        # Create teacher availability for tomorrow
        tomorrow = timezone.now().date() + timedelta(days=1)
        weekday = tomorrow.weekday()
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=weekday,
            start_time='09:00',
            end_time='17:00',
        )

        stale_trial = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-4)
        stale_trial.lesson_type = Booking.LessonType.TRIAL
        stale_trial.save()

        from zoneinfo import ZoneInfo
        teacher_tz = ZoneInfo(self.teacher.user.timezone)
        slot_dt = timezone.datetime.combine(tomorrow, timezone.datetime.min.time(), tzinfo=teacher_tz).replace(hour=10)

        response = self.student_client.post(
            reverse('booking:book_slot'),
            {'start_at': slot_dt.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

        new_booking = Booking.objects.get(id=data['booking_id'])
        self.assertEqual(new_booking.lesson_type, Booking.LessonType.TRIAL)
        self.assertEqual(new_booking.status, Booking.Status.PENDING)

    def test_active_future_pending_trial_still_blocks_second_trial(self):
        self.teacher.offers_trial = True
        self.teacher.save()

        future_trial = self._make_booking(status=Booking.Status.PENDING, hours_from_now=5)
        future_trial.lesson_type = Booking.LessonType.TRIAL
        future_trial.save()

        lesson_type, price, duration = get_lesson_type_and_price(self.teacher, self.student_user)
        self.assertEqual(lesson_type, Booking.LessonType.REGULAR)
        self.assertEqual(price, self.teacher.lesson_price)

    # 7. Calendar View & Expiration
    def test_teacher_calendar_expires_stale_bookings(self):
        stale_booking = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-2)
        response = self.teacher_client.get(reverse('booking:teacher_calendar'))
        self.assertEqual(response.status_code, 200)

        stale_booking.refresh_from_db()
        self.assertEqual(stale_booking.status, Booking.Status.EXPIRED)
        self.assertContains(response, 'data-status="expired"')
        self.assertContains(response, 'value="expired"')

    def test_student_calendar_expires_stale_bookings(self):
        stale_booking = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-2)
        response = self.student_client.get(reverse('booking:student_calendar'))
        self.assertEqual(response.status_code, 200)

        stale_booking.refresh_from_db()
        self.assertEqual(stale_booking.status, Booking.Status.EXPIRED)
        self.assertContains(response, 'data-status="expired"')
        self.assertContains(response, 'value="expired"')

    def test_teacher_calendar_ajax_expires_stale_bookings(self):
        stale_booking = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-2)
        response = self.teacher_client.get(reverse('booking:teacher_calendar_ajax'))
        self.assertEqual(response.status_code, 200)

        stale_booking.refresh_from_db()
        self.assertEqual(stale_booking.status, Booking.Status.EXPIRED)
        self.assertContains(response, 'data-status="expired"')

    def test_lesson_detail_modal_shows_expired_status_badge(self):
        expired_booking = self._make_booking(status=Booking.Status.EXPIRED, hours_from_now=-5)

        # Teacher modal
        t_response = self.teacher_client.get(
            reverse('booking:lesson_detail', kwargs={'booking_id': expired_booking.id})
        )
        self.assertEqual(t_response.status_code, 200)
        self.assertContains(t_response, 'data-status="expired"')
        self.assertContains(t_response, 'Expired')

        # Student modal
        s_response = self.student_client.get(
            reverse('booking:lesson_detail_student', kwargs={'booking_id': expired_booking.id})
        )
        self.assertEqual(s_response.status_code, 200)
        self.assertContains(s_response, 'data-status="expired"')
        self.assertContains(s_response, 'Expired')

    # 8. Management Command
    def test_expire_pending_bookings_management_command(self):
        stale1 = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-2)
        stale2 = self._make_booking(status=Booking.Status.PENDING, hours_from_now=-5)
        future = self._make_booking(status=Booking.Status.PENDING, hours_from_now=3)

        out = io.StringIO()
        call_command('expire_pending_bookings', stdout=out)

        output = out.getvalue()
        self.assertIn('Successfully expired 2 pending booking(s).', output)

        stale1.refresh_from_db()
        stale2.refresh_from_db()
        future.refresh_from_db()

        self.assertEqual(stale1.status, Booking.Status.EXPIRED)
        self.assertEqual(stale2.status, Booking.Status.EXPIRED)
        self.assertEqual(future.status, Booking.Status.PENDING)

        # Running again finds 0
        out_second = io.StringIO()
        call_command('expire_pending_bookings', stdout=out_second)
        self.assertIn('Successfully expired 0 pending booking(s).', out_second.getvalue())



