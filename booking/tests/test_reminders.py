import smtplib
from datetime import date, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core import mail
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from booking.models import Booking
from booking.tasks import (
    expire_pending_bookings_task,
    send_booking_reminder_1h_student_email_task,
    send_booking_reminder_1h_teacher_email_task,
    send_booking_reminder_24h_student_email_task,
    send_lesson_reminders,
)


class LessonReminderEmailTasksTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.meeting_link = 'https://meet.google.com/test-room'
        cls.teacher.save()

        cls.teacher_user.email = 'teacher@englishwithmary.ir'
        cls.teacher_user.first_name = 'Mary'
        cls.teacher_user.last_name = 'Smith'
        cls.teacher_user.timezone = 'Asia/Tehran'
        cls.teacher_user.save()

        cls.student_user.email = 'student@example.com'
        cls.student_user.first_name = 'John'
        cls.student_user.last_name = 'Doe'
        cls.student_user.timezone = 'America/New_York'
        cls.student_user.save()

    def setUp(self):
        super().setUp()
        self.teacher_tz = ZoneInfo(self.teacher_user.timezone)
        self.student_tz = ZoneInfo(self.student_user.timezone)

        # 2 days in the future
        future_start = timezone.now() + timedelta(days=2)
        self.booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=future_start,
            end_at=future_start + timedelta(minutes=60),
            lesson_type=Booking.LessonType.REGULAR,
            price=self.teacher.lesson_price,
            status=Booking.Status.CONFIRMED,
        )

    # --- 24h Student Reminder Task Tests ---

    def test_send_24h_student_reminder_success(self):
        send_booking_reminder_24h_student_email_task(self.booking.id)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['student@example.com'])
        self.assertIn('Lesson Reminder', email.subject)
        self.assertIn('Tomorrow', email.subject)
        self.assertIn('Mary Smith', email.subject)

        # Check localized time in student timezone
        local_start = timezone.localtime(self.booking.start_at, self.student_tz)
        local_end = timezone.localtime(self.booking.end_at, self.student_tz)
        expected_time_str = f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}"
        expected_date_str = local_start.strftime('%A, %B %d, %Y')

        self.assertIn(expected_date_str, email.body)
        self.assertIn(expected_time_str, email.body)
        self.assertIn('America/New_York', email.body)
        self.assertIn('https://meet.google.com/test-room', email.body)

        # Check HTML alternative
        self.assertEqual(len(email.alternatives), 1)
        html_content, mimetype = email.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertIn(expected_date_str, html_content)
        self.assertIn(expected_time_str, html_content)
        self.assertIn('https://meet.google.com/test-room', html_content)

    def test_send_24h_student_reminder_fallback_without_meeting_link(self):
        self.teacher.meeting_link = ''
        self.teacher.save()

        send_booking_reminder_24h_student_email_task(self.booking.id)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn('share the meeting link with you prior', email.body)
        self.assertIn('share the meeting link with you prior', email.alternatives[0][0])

    def test_send_24h_student_reminder_skips_non_confirmed_status(self):
        self.booking.status = Booking.Status.CANCELLED
        self.booking.save()

        send_booking_reminder_24h_student_email_task(self.booking.id)
        self.assertEqual(len(mail.outbox), 0)

        self.booking.status = Booking.Status.PENDING
        self.booking.save()

        send_booking_reminder_24h_student_email_task(self.booking.id)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_24h_student_reminder_missing_email_skips(self):
        self.student_user.email = ''
        self.student_user.save()

        send_booking_reminder_24h_student_email_task(self.booking.id)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_24h_student_reminder_retries_on_transient_error(self):
        with patch('booking.emails.send_booking_reminder_24h_student_email', side_effect=smtplib.SMTPException('SMTP error')):
            with self.assertRaises(Exception):
                send_booking_reminder_24h_student_email_task.apply(args=[self.booking.id], throw=True)

    # --- 1h Student Reminder Task Tests ---

    def test_send_1h_student_reminder_success(self):
        send_booking_reminder_1h_student_email_task(self.booking.id)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['student@example.com'])
        self.assertIn('Lesson Starting in 1 Hour', email.subject)
        self.assertIn('Mary Smith', email.subject)

        # Check localized time in student timezone
        local_start = timezone.localtime(self.booking.start_at, self.student_tz)
        local_end = timezone.localtime(self.booking.end_at, self.student_tz)
        expected_time_str = f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}"
        expected_date_str = local_start.strftime('%A, %B %d, %Y')

        self.assertIn(expected_date_str, email.body)
        self.assertIn(expected_time_str, email.body)
        self.assertIn('America/New_York', email.body)
        self.assertIn('https://meet.google.com/test-room', email.body)

        html_content = email.alternatives[0][0]
        self.assertIn('Join Lesson Now', html_content)
        self.assertIn('https://meet.google.com/test-room', html_content)

    def test_send_1h_student_reminder_skips_non_confirmed_status(self):
        self.booking.status = Booking.Status.EXPIRED
        self.booking.save()

        send_booking_reminder_1h_student_email_task(self.booking.id)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_1h_student_reminder_missing_email_skips(self):
        self.student_user.email = ''
        self.student_user.save()

        send_booking_reminder_1h_student_email_task(self.booking.id)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_1h_student_reminder_retries_on_transient_error(self):
        with patch('booking.emails.send_booking_reminder_1h_student_email', side_effect=smtplib.SMTPException('SMTP error')):
            with self.assertRaises(Exception):
                send_booking_reminder_1h_student_email_task.apply(args=[self.booking.id], throw=True)

    # --- 1h Teacher Reminder Task Tests ---

    def test_send_1h_teacher_reminder_success(self):
        send_booking_reminder_1h_teacher_email_task(self.booking.id)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['teacher@englishwithmary.ir'])
        self.assertIn('Upcoming Lesson in 1 Hour', email.subject)
        self.assertIn('John Doe', email.subject)

        # Check localized time in teacher timezone
        local_start = timezone.localtime(self.booking.start_at, self.teacher_tz)
        local_end = timezone.localtime(self.booking.end_at, self.teacher_tz)
        expected_time_str = f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}"
        expected_date_str = local_start.strftime('%A, %B %d, %Y')

        self.assertIn(expected_date_str, email.body)
        self.assertIn(expected_time_str, email.body)
        self.assertIn('Asia/Tehran', email.body)
        self.assertIn('John Doe', email.body)
        self.assertIn('https://meet.google.com/test-room', email.body)

        html_content = email.alternatives[0][0]
        self.assertIn('Open Classroom', html_content)
        self.assertIn('https://meet.google.com/test-room', html_content)

    def test_send_1h_teacher_reminder_skips_non_confirmed_status(self):
        self.booking.status = Booking.Status.CANCELLED
        self.booking.save()

        send_booking_reminder_1h_teacher_email_task(self.booking.id)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_1h_teacher_reminder_missing_email_skips(self):
        self.teacher_user.email = ''
        self.teacher_user.save()
        self.teacher.contact_email = ''
        self.teacher.save()

        send_booking_reminder_1h_teacher_email_task(self.booking.id)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_1h_teacher_reminder_retries_on_transient_error(self):
        with patch('booking.emails.send_booking_reminder_1h_teacher_email', side_effect=smtplib.SMTPException('SMTP error')):
            with self.assertRaises(Exception):
                send_booking_reminder_1h_teacher_email_task.apply(args=[self.booking.id], throw=True)


class LessonReminderSweeperTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.meeting_link = 'https://meet.google.com/test-room'
        cls.teacher.save()

        cls.teacher_user.email = 'teacher@englishwithmary.ir'
        cls.teacher_user.first_name = 'Mary'
        cls.teacher_user.last_name = 'Smith'
        cls.teacher_user.timezone = 'Asia/Tehran'
        cls.teacher_user.save()

        cls.student_user.email = 'student@example.com'
        cls.student_user.first_name = 'John'
        cls.student_user.last_name = 'Doe'
        cls.student_user.timezone = 'America/New_York'
        cls.student_user.save()

    def _create_booking(self, start_at, status=Booking.Status.CONFIRMED, created_at=None):
        booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=60),
            lesson_type=Booking.LessonType.REGULAR,
            price=self.teacher.lesson_price,
            status=status,
        )
        if created_at is not None:
            Booking.objects.filter(pk=booking.pk).update(created_at=created_at)
            booking.refresh_from_db()
        return booking

    def test_24h_reminder_sweep_matches_eligible_booking_student_only(self):
        """24h reminder matches booking 24h away, created in advance, and sends to student only."""
        now = timezone.now()
        # Booking created 2 days ago, scheduled 24 hours from now
        booking_24h = self._create_booking(
            start_at=now + timedelta(hours=24),
            created_at=now - timedelta(days=2),
        )

        result = send_lesson_reminders(now=now)

        self.assertEqual(result['reminders_24h'], 1)
        booking_24h.refresh_from_db()
        self.assertTrue(booking_24h.reminder_24h_sent)
        self.assertFalse(booking_24h.reminder_1h_sent)

        # Must send exactly 1 email to student ONLY (no teacher 24h reminder to avoid alert fatigue)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['student@example.com'])
        self.assertIn('Tomorrow', mail.outbox[0].subject)

    def test_24h_reminder_sweep_bounds_exclude_out_of_window_bookings(self):
        """Bookings too far in the future or too close are not matched by the 24h sweep."""
        now = timezone.now()
        # 26 hours away (not yet in 23-25h window)
        booking_26h = self._create_booking(
            start_at=now + timedelta(hours=26),
            created_at=now - timedelta(days=2),
        )
        # 22 hours away (already past 23-25h window)
        booking_22h = self._create_booking(
            start_at=now + timedelta(hours=22),
            created_at=now - timedelta(days=2),
        )

        result = send_lesson_reminders(now=now)

        self.assertEqual(result['reminders_24h'], 0)
        self.assertEqual(len(mail.outbox), 0)
        booking_26h.refresh_from_db()
        self.assertFalse(booking_26h.reminder_24h_sent)

    def test_24h_reminder_sweep_suppresses_short_notice_bookings(self):
        """Bookings created <24 hours before start do not trigger 24h reminders."""
        now = timezone.now()
        # Booked 12 hours before start (instant tutoring / same-day)
        booking_short_notice = self._create_booking(
            start_at=now + timedelta(hours=12),
            created_at=now - timedelta(hours=1),
        )
        # Booked 23.5 hours before start (inside 23-25h window, but lead time < 24h)
        booking_just_under_24h = self._create_booking(
            start_at=now + timedelta(hours=23, minutes=30),
            created_at=now - timedelta(minutes=10),
        )

        result = send_lesson_reminders(now=now)

        self.assertEqual(result['reminders_24h'], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_1h_reminder_sweep_matches_and_sends_to_both_student_and_teacher(self):
        """1h reminder matches booking 50 minutes away and dispatches to both student and teacher."""
        now = timezone.now()
        booking_1h = self._create_booking(
            start_at=now + timedelta(minutes=50),
            created_at=now - timedelta(days=1),
        )

        result = send_lesson_reminders(now=now)

        self.assertEqual(result['reminders_1h'], 1)
        booking_1h.refresh_from_db()
        self.assertTrue(booking_1h.reminder_1h_sent)

        self.assertEqual(len(mail.outbox), 2)
        recipients = {email.to[0] for email in mail.outbox}
        self.assertEqual(recipients, {'student@example.com', 'teacher@englishwithmary.ir'})

        student_mail = next(e for e in mail.outbox if e.to == ['student@example.com'])
        teacher_mail = next(e for e in mail.outbox if e.to == ['teacher@englishwithmary.ir'])

        self.assertIn('1 Hour', student_mail.subject)
        self.assertIn('1 Hour', teacher_mail.subject)
        self.assertIn('https://meet.google.com/test-room', student_mail.body)
        self.assertIn('https://meet.google.com/test-room', teacher_mail.body)

    def test_1h_reminder_sweep_excludes_bookings_outside_window(self):
        """Bookings starting in 1h 20m or past bookings are excluded from 1h sweep."""
        now = timezone.now()
        # Booking 80 minutes away (> 1h 5m)
        booking_80m = self._create_booking(
            start_at=now + timedelta(minutes=80),
            created_at=now - timedelta(days=1),
        )
        # Past booking
        booking_past = self._create_booking(
            start_at=now - timedelta(minutes=10),
            created_at=now - timedelta(days=1),
        )

        result = send_lesson_reminders(now=now)

        self.assertEqual(result['reminders_1h'], 0)
        self.assertEqual(len(mail.outbox), 0)
        booking_80m.refresh_from_db()
        self.assertFalse(booking_80m.reminder_1h_sent)

    def test_sweeper_excludes_pending_bookings(self):
        """Pending bookings never receive 24h or 1h reminders."""
        now = timezone.now()
        booking_24h_pending = self._create_booking(
            start_at=now + timedelta(hours=24),
            status=Booking.Status.PENDING,
            created_at=now - timedelta(days=2),
        )
        booking_1h_pending = self._create_booking(
            start_at=now + timedelta(minutes=45),
            status=Booking.Status.PENDING,
            created_at=now - timedelta(days=1),
        )

        result = send_lesson_reminders(now=now)

        self.assertEqual(result['reminders_24h'], 0)
        self.assertEqual(result['reminders_1h'], 0)
        self.assertEqual(len(mail.outbox), 0)
        booking_24h_pending.refresh_from_db()
        booking_1h_pending.refresh_from_db()
        self.assertFalse(booking_24h_pending.reminder_24h_sent)
        self.assertFalse(booking_1h_pending.reminder_1h_sent)

    def test_sweeper_excludes_cancelled_bookings(self):
        """Cancelled bookings never receive 24h or 1h reminders."""
        now = timezone.now()
        booking_24h_cancelled = self._create_booking(
            start_at=now + timedelta(hours=24),
            status=Booking.Status.CANCELLED,
            created_at=now - timedelta(days=2),
        )
        booking_1h_cancelled = self._create_booking(
            start_at=now + timedelta(minutes=45),
            status=Booking.Status.CANCELLED,
            created_at=now - timedelta(days=1),
        )

        result = send_lesson_reminders(now=now)

        self.assertEqual(result['reminders_24h'], 0)
        self.assertEqual(result['reminders_1h'], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_sweeper_excludes_expired_bookings(self):
        """Expired bookings never receive 24h or 1h reminders."""
        now = timezone.now()
        booking_24h_expired = self._create_booking(
            start_at=now + timedelta(hours=24),
            status=Booking.Status.EXPIRED,
            created_at=now - timedelta(days=2),
        )
        booking_1h_expired = self._create_booking(
            start_at=now + timedelta(minutes=45),
            status=Booking.Status.EXPIRED,
            created_at=now - timedelta(days=1),
        )

        result = send_lesson_reminders(now=now)

        self.assertEqual(result['reminders_24h'], 0)
        self.assertEqual(result['reminders_1h'], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_sweeper_idempotency_consecutive_runs(self):
        """Consecutive sweeper ticks do not dispatch duplicate reminder emails."""
        now = timezone.now()
        booking_24h = self._create_booking(
            start_at=now + timedelta(hours=24),
            created_at=now - timedelta(days=2),
        )
        booking_1h = self._create_booking(
            start_at=now + timedelta(minutes=45),
            created_at=now - timedelta(days=1),
        )

        # First run dispatches
        first_run = send_lesson_reminders(now=now)
        self.assertEqual(first_run['reminders_24h'], 1)
        self.assertEqual(first_run['reminders_1h'], 1)
        self.assertEqual(len(mail.outbox), 3)  # 1 student 24h + 1 student 1h + 1 teacher 1h

        # Second run immediately afterwards
        second_run = send_lesson_reminders(now=now + timedelta(minutes=1))
        self.assertEqual(second_run['reminders_24h'], 0)
        self.assertEqual(second_run['reminders_1h'], 0)
        self.assertEqual(len(mail.outbox), 3)  # No new emails sent

        # Third run at 5 minutes interval
        third_run = send_lesson_reminders(now=now + timedelta(minutes=5))
        self.assertEqual(third_run['reminders_24h'], 0)
        self.assertEqual(third_run['reminders_1h'], 0)
        self.assertEqual(len(mail.outbox), 3)

    def test_expire_pending_bookings_periodic_task(self):
        """expire_pending_bookings_task transitions stale pending bookings to EXPIRED."""
        now = timezone.now()
        # Stale pending booking in the past
        stale_pending = self._create_booking(
            start_at=now - timedelta(hours=2),
            status=Booking.Status.PENDING,
        )
        # Future pending booking
        future_pending = self._create_booking(
            start_at=now + timedelta(hours=3),
            status=Booking.Status.PENDING,
        )
        # Past confirmed booking at a non-overlapping past time
        past_confirmed = self._create_booking(
            start_at=now - timedelta(hours=5),
            status=Booking.Status.CONFIRMED,
        )

        expired_count = expire_pending_bookings_task()

        self.assertEqual(expired_count, 1)
        stale_pending.refresh_from_db()
        future_pending.refresh_from_db()
        past_confirmed.refresh_from_db()

        self.assertEqual(stale_pending.status, Booking.Status.EXPIRED)
        self.assertEqual(future_pending.status, Booking.Status.PENDING)
        self.assertEqual(past_confirmed.status, Booking.Status.CONFIRMED)


class CeleryBeatScheduleConfigurationTests(RoleTestCase):
    def test_celery_beat_schedule_configured(self):
        from tutor_booking.celery import app

        schedule = app.conf.beat_schedule
        self.assertIn('send-lesson-reminders-every-5-minutes', schedule)
        self.assertEqual(
            schedule['send-lesson-reminders-every-5-minutes']['task'],
            'booking.tasks.send_lesson_reminders',
        )
        self.assertEqual(
            schedule['send-lesson-reminders-every-5-minutes']['schedule'],
            300.0,
        )

        self.assertIn('expire-pending-bookings-every-15-minutes', schedule)
        self.assertEqual(
            schedule['expire-pending-bookings-every-15-minutes']['task'],
            'booking.tasks.expire_pending_bookings_task',
        )
        self.assertEqual(
            schedule['expire-pending-bookings-every-15-minutes']['schedule'],
            900.0,
        )

