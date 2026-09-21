from datetime import date, datetime, time, timedelta
import smtplib
from unittest.mock import patch
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core import mail
from django.db import IntegrityError
from django.utils import timezone
from accounts.tests.base import RoleTestCase
from booking.emails import send_teacher_daily_digest_email
from booking.models import Booking, TeacherDailyDigestRecord
from booking.tasks import (
    send_daily_schedule_digests,
    send_teacher_daily_digest_email_task,
)


class TeacherDailyDigestRecordModelTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile

    def test_create_digest_record(self):
        record = TeacherDailyDigestRecord.objects.create(
            teacher=self.teacher,
            target_date=date(2026, 9, 22),
            booking_count=3,
            status=TeacherDailyDigestRecord.Status.SENT,
        )
        self.assertEqual(record.teacher, self.teacher)
        self.assertEqual(record.target_date, date(2026, 9, 22))
        self.assertEqual(record.booking_count, 3)
        self.assertEqual(record.status, TeacherDailyDigestRecord.Status.SENT)
        self.assertIn("Daily Digest", str(record))

    def test_unique_constraint_on_teacher_and_target_date(self):
        TeacherDailyDigestRecord.objects.create(
            teacher=self.teacher,
            target_date=date(2026, 9, 22),
            booking_count=1,
            status=TeacherDailyDigestRecord.Status.SENT,
        )
        with self.assertRaises(IntegrityError):
            TeacherDailyDigestRecord.objects.create(
                teacher=self.teacher,
                target_date=date(2026, 9, 22),
                booking_count=2,
                status=TeacherDailyDigestRecord.Status.SENT,
            )


class TeacherDailyDigestEmailRenderingTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.meeting_link = 'https://meet.google.com/test-digest-room'
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
        self.target_date = date(2026, 9, 22)

        # Create 2 confirmed bookings on target_date in teacher's timezone (Asia/Tehran)
        # 10:00 Tehran is 06:30 UTC
        start_1 = datetime(2026, 9, 22, 10, 0, tzinfo=self.teacher_tz)
        end_1 = start_1 + timedelta(minutes=50)
        self.booking_1 = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start_1,
            end_at=end_1,
            lesson_type=Booking.LessonType.REGULAR,
            price=self.teacher.lesson_price,
            status=Booking.Status.CONFIRMED,
        )

        # 14:00 Tehran is 10:30 UTC
        start_2 = datetime(2026, 9, 22, 14, 0, tzinfo=self.teacher_tz)
        end_2 = start_2 + timedelta(minutes=25)
        self.booking_2 = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start_2,
            end_at=end_2,
            lesson_type=Booking.LessonType.TRIAL,
            price=self.teacher.trial_price,
            status=Booking.Status.CONFIRMED,
        )

    def test_send_daily_digest_email_with_bookings(self):
        sent = send_teacher_daily_digest_email(
            teacher=self.teacher,
            target_date=self.target_date,
            bookings=[self.booking_1, self.booking_2],
        )
        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)

        email = mail.outbox[0]
        self.assertEqual(email.to, ['teacher@englishwithmary.ir'])
        self.assertIn('Daily Schedule Digest', email.subject)
        self.assertIn('2 lessons scheduled', email.subject)

        # Check plain text content
        self.assertIn('Mary', email.body)
        self.assertIn('John Doe', email.body)
        self.assertIn('10:00 – 10:50', email.body)
        self.assertIn('Regular Lesson', email.body)
        self.assertIn('14:00 – 14:25', email.body)
        self.assertIn('Trial Lesson', email.body)
        self.assertIn('https://meet.google.com/test-digest-room', email.body)
        self.assertIn('Asia/Tehran', email.body)

        # Check HTML alternative
        self.assertEqual(len(email.alternatives), 1)
        html_content, mimetype = email.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertIn('John Doe', html_content)
        self.assertIn('10:00 – 10:50', html_content)
        self.assertIn('14:00 – 14:25', html_content)
        self.assertIn('https://meet.google.com/test-digest-room', html_content)

    def test_send_daily_digest_email_empty_schedule(self):
        sent = send_teacher_daily_digest_email(
            teacher=self.teacher,
            target_date=self.target_date,
            bookings=[],
        )
        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn('No lessons scheduled', email.subject)
        self.assertIn('no lessons scheduled for tomorrow', email.body.lower())

    def test_send_daily_digest_email_teacher_no_email(self):
        self.teacher_user.email = ''
        self.teacher_user.save()
        self.teacher.contact_email = ''
        self.teacher.save()

        sent = send_teacher_daily_digest_email(
            teacher=self.teacher,
            target_date=self.target_date,
            bookings=[self.booking_1],
        )
        self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_daily_digest_email_contact_email_fallback(self):
        self.teacher_user.email = ''
        self.teacher_user.save()
        self.teacher.contact_email = 'contact@englishwithmary.ir'
        self.teacher.save()

        sent = send_teacher_daily_digest_email(
            teacher=self.teacher,
            target_date=self.target_date,
            bookings=[self.booking_1],
        )
        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['contact@englishwithmary.ir'])


class TeacherDailyDigestTasksTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.meeting_link = 'https://meet.google.com/test-digest-room'
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
        self.target_date = date(2026, 9, 22)

    def test_worker_task_sends_email_and_records_idempotency(self):
        # 10:00 Tehran on target_date
        start = datetime(2026, 9, 22, 10, 0, tzinfo=self.teacher_tz)
        booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start,
            end_at=start + timedelta(minutes=50),
            status=Booking.Status.CONFIRMED,
        )

        send_teacher_daily_digest_email_task(self.teacher.id, self.target_date.isoformat())

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['teacher@englishwithmary.ir'])

        record = TeacherDailyDigestRecord.objects.get(teacher=self.teacher, target_date=self.target_date)
        self.assertEqual(record.booking_count, 1)
        self.assertEqual(record.status, TeacherDailyDigestRecord.Status.SENT)

    def test_worker_task_idempotency_prevents_duplicate_send(self):
        # Pre-create record
        TeacherDailyDigestRecord.objects.create(
            teacher=self.teacher,
            target_date=self.target_date,
            booking_count=1,
            status=TeacherDailyDigestRecord.Status.SENT,
        )

        send_teacher_daily_digest_email_task(self.teacher.id, self.target_date.isoformat())

        # No email sent
        self.assertEqual(len(mail.outbox), 0)

    def test_worker_task_skips_empty_when_no_bookings(self):
        send_teacher_daily_digest_email_task(self.teacher.id, self.target_date.isoformat())

        # No email sent because no bookings exist and skip_empty=True by default
        self.assertEqual(len(mail.outbox), 0)

        record = TeacherDailyDigestRecord.objects.get(teacher=self.teacher, target_date=self.target_date)
        self.assertEqual(record.booking_count, 0)
        self.assertEqual(record.status, TeacherDailyDigestRecord.Status.SKIPPED_EMPTY)

    def test_worker_task_nonexistent_teacher_graceful(self):
        # Teacher ID 999999 does not exist
        send_teacher_daily_digest_email_task(999999, self.target_date.isoformat())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(TeacherDailyDigestRecord.objects.count(), 0)

    @patch('booking.emails.send_teacher_daily_digest_email')
    def test_worker_task_retries_on_transient_error(self, mock_send_email):
        mock_send_email.side_effect = smtplib.SMTPException("Transient SMTP failure")

        start = datetime(2026, 9, 22, 10, 0, tzinfo=self.teacher_tz)
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start,
            end_at=start + timedelta(minutes=50),
            status=Booking.Status.CONFIRMED,
        )

        with self.assertRaises(smtplib.SMTPException):
            send_teacher_daily_digest_email_task(self.teacher.id, self.target_date.isoformat())

        # Record should not be created if email sending failed
        self.assertFalse(
            TeacherDailyDigestRecord.objects.filter(
                teacher=self.teacher, target_date=self.target_date
            ).exists()
        )

    def test_periodic_sweeper_dispatches_when_local_hour_matches(self):
        # Teacher is Asia/Tehran (UTC+3:30).
        # At 2026-09-21 16:30 UTC, it is 20:00 Tehran time (hour == 20).
        now = datetime(2026, 9, 21, 16, 30, tzinfo=ZoneInfo('UTC'))

        # Confirmed booking for tomorrow (2026-09-22) in Tehran
        tomorrow_start = datetime(2026, 9, 22, 11, 0, tzinfo=self.teacher_tz)
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=tomorrow_start,
            end_at=tomorrow_start + timedelta(minutes=50),
            status=Booking.Status.CONFIRMED,
        )

        stats = send_daily_schedule_digests(now=now)
        self.assertEqual(stats['dispatched_digests'], 1)
        self.assertEqual(len(mail.outbox), 1)

        record = TeacherDailyDigestRecord.objects.get(teacher=self.teacher, target_date=date(2026, 9, 22))
        self.assertEqual(record.status, TeacherDailyDigestRecord.Status.SENT)
        self.assertEqual(record.booking_count, 1)

    def test_periodic_sweeper_does_not_dispatch_when_local_hour_does_not_match(self):
        # At 2026-09-21 15:00 UTC, it is 18:30 Tehran time (hour == 18, not 20).
        now = datetime(2026, 9, 21, 15, 0, tzinfo=ZoneInfo('UTC'))

        tomorrow_start = datetime(2026, 9, 22, 11, 0, tzinfo=self.teacher_tz)
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=tomorrow_start,
            end_at=tomorrow_start + timedelta(minutes=50),
            status=Booking.Status.CONFIRMED,
        )

        stats = send_daily_schedule_digests(now=now)
        self.assertEqual(stats['dispatched_digests'], 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(TeacherDailyDigestRecord.objects.filter(teacher=self.teacher).exists())

    def test_periodic_sweeper_idempotent_across_repeated_ticks(self):
        # Tick 1 at 20:00 local time
        now_1 = datetime(2026, 9, 21, 16, 30, tzinfo=ZoneInfo('UTC'))
        tomorrow_start = datetime(2026, 9, 22, 11, 0, tzinfo=self.teacher_tz)
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=tomorrow_start,
            end_at=tomorrow_start + timedelta(minutes=50),
            status=Booking.Status.CONFIRMED,
        )

        stats_1 = send_daily_schedule_digests(now=now_1)
        self.assertEqual(stats_1['dispatched_digests'], 1)
        self.assertEqual(len(mail.outbox), 1)

        # Tick 2 at 20:30 local time (same day)
        now_2 = datetime(2026, 9, 21, 17, 0, tzinfo=ZoneInfo('UTC'))
        stats_2 = send_daily_schedule_digests(now=now_2)
        self.assertEqual(stats_2['dispatched_digests'], 0)
        # Mail outbox still has only 1 email
        self.assertEqual(len(mail.outbox), 1)

    def test_timezone_boundary_teacher_vs_student(self):
        # Teacher is Asia/Tehran (UTC+3:30).
        # Student is America/New_York (UTC-4:00 EDT in September).
        # Target date is 2026-09-22 in Tehran.

        # Lesson A: 2026-09-22 01:00 Tehran time.
        # In UTC: 2026-09-21 21:30 UTC.
        # In New York: 2026-09-21 17:30 EDT (Student's previous calendar day!).
        # But in teacher's timezone, this is on Sep 22, so it MUST be included!
        start_a = datetime(2026, 9, 22, 1, 0, tzinfo=self.teacher_tz)
        booking_a = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start_a,
            end_at=start_a + timedelta(minutes=50),
            status=Booking.Status.CONFIRMED,
        )

        # Lesson B: 2026-09-23 00:30 Tehran time.
        # In UTC: 2026-09-22 21:00 UTC.
        # In New York: 2026-09-22 17:00 EDT (Student's Sep 22!).
        # But in teacher's timezone, this is on Sep 23, so it MUST NOT be included!
        start_b = datetime(2026, 9, 23, 0, 30, tzinfo=self.teacher_tz)
        booking_b = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start_b,
            end_at=start_b + timedelta(minutes=50),
            status=Booking.Status.CONFIRMED,
        )

        # Dispatch for 2026-09-22
        send_teacher_daily_digest_email_task(self.teacher.id, date(2026, 9, 22).isoformat())

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn('1 lesson scheduled', email.subject)
        self.assertIn('01:00 – 01:50', email.body)
        self.assertNotIn('00:30', email.body)

    def test_excludes_cancelled_pending_expired_bookings(self):
        # Confirmed
        start_1 = datetime(2026, 9, 22, 10, 0, tzinfo=self.teacher_tz)
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start_1,
            end_at=start_1 + timedelta(minutes=50),
            status=Booking.Status.CONFIRMED,
        )

        # Cancelled
        start_2 = datetime(2026, 9, 22, 12, 0, tzinfo=self.teacher_tz)
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start_2,
            end_at=start_2 + timedelta(minutes=50),
            status=Booking.Status.CANCELLED,
        )

        # Pending
        start_3 = datetime(2026, 9, 22, 14, 0, tzinfo=self.teacher_tz)
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start_3,
            end_at=start_3 + timedelta(minutes=50),
            status=Booking.Status.PENDING,
        )

        # Expired
        start_4 = datetime(2026, 9, 22, 16, 0, tzinfo=self.teacher_tz)
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start_4,
            end_at=start_4 + timedelta(minutes=50),
            status=Booking.Status.EXPIRED,
        )

        send_teacher_daily_digest_email_task(self.teacher.id, date(2026, 9, 22).isoformat())

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        # Only 1 lesson should be counted and shown
        self.assertIn('1 lesson scheduled', email.subject)
        self.assertIn('10:00 – 10:50', email.body)
        self.assertNotIn('12:00', email.body)
        self.assertNotIn('14:00', email.body)
        self.assertNotIn('16:00', email.body)


class TeacherDailyDigestBeatScheduleTests(RoleTestCase):
    def test_beat_schedule_configured(self):
        beat_schedule = getattr(settings, 'CELERY_BEAT_SCHEDULE', {})
        self.assertIn('send-teacher-daily-digest-hourly', beat_schedule)
        entry = beat_schedule['send-teacher-daily-digest-hourly']
        self.assertEqual(entry['task'], 'booking.tasks.send_daily_schedule_digests')
        self.assertEqual(entry['schedule'], 3600.0)
