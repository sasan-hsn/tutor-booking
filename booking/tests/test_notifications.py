import smtplib
from datetime import date, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from booking.models import Booking, RegularAvailability
from booking.tasks import (
    send_booking_request_notifications,
    send_booking_request_student_email_task,
    send_booking_request_teacher_email_task,
)


class BookingNotificationTasksTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.lesson_price = 20
        cls.teacher.lesson_duration_minutes = 60
        cls.teacher.offers_trial = True
        cls.teacher.trial_price = 5
        cls.teacher.trial_duration_minutes = 30
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

        # 2 days in the future at 14:00 UTC
        future_date = timezone.localdate() + timedelta(days=2)
        self.start_at = timezone.datetime.combine(
            future_date, time(14, 0), tzinfo=ZoneInfo('UTC')
        )
        self.end_at = self.start_at + timedelta(minutes=30)

        self.booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=self.start_at,
            end_at=self.end_at,
            lesson_type=Booking.LessonType.TRIAL,
            price=self.teacher.trial_price,
            status=Booking.Status.PENDING,
        )

    def test_send_booking_request_student_email_success(self):
        send_booking_request_student_email_task(self.booking.id)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['student@example.com'])
        self.assertIn('Lesson Request Received', email.subject)
        self.assertIn('Mary Smith', email.subject)

        # Check localized time in student's timezone (America/New_York = UTC-4 in summer / UTC-5 in winter)
        local_start = timezone.localtime(self.start_at, self.student_tz)
        local_end = timezone.localtime(self.end_at, self.student_tz)
        expected_time_str = f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}"
        expected_date_str = local_start.strftime('%A, %B %d, %Y')

        self.assertIn(expected_date_str, email.body)
        self.assertIn(expected_time_str, email.body)
        self.assertIn('America/New_York', email.body)
        self.assertIn('John', email.body)
        self.assertIn('Mary Smith', email.body)
        self.assertIn('Trial Lesson', email.body)
        self.assertIn('pending', email.body.lower())

        # Check multipart HTML alternative
        self.assertEqual(len(email.alternatives), 1)
        html_content, mimetype = email.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertIn(expected_date_str, html_content)
        self.assertIn(expected_time_str, html_content)
        self.assertIn('America/New_York', html_content)
        self.assertIn('John', html_content)
        self.assertIn('Mary Smith', html_content)
        self.assertIn('Trial Lesson', html_content)
        self.assertIn('Pending', html_content)

    def test_send_booking_request_teacher_email_success(self):
        send_booking_request_teacher_email_task(self.booking.id)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['teacher@englishwithmary.ir'])
        self.assertIn('New Lesson Request from John Doe', email.subject)

        # Check localized time in teacher's timezone (Asia/Tehran = UTC+3:30)
        local_start = timezone.localtime(self.start_at, self.teacher_tz)
        local_end = timezone.localtime(self.end_at, self.teacher_tz)
        expected_time_str = f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}"
        expected_date_str = local_start.strftime('%A, %B %d, %Y')

        self.assertIn(expected_date_str, email.body)
        self.assertIn(expected_time_str, email.body)
        self.assertIn('Asia/Tehran', email.body)
        self.assertIn('John Doe', email.body)
        self.assertIn('student@example.com', email.body)
        self.assertIn('Trial Lesson', email.body)
        self.assertIn(reverse('booking:teacher_dashboard'), email.body)

        # Check multipart HTML alternative
        self.assertEqual(len(email.alternatives), 1)
        html_content, mimetype = email.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertIn(expected_date_str, html_content)
        self.assertIn(expected_time_str, html_content)
        self.assertIn('Asia/Tehran', html_content)
        self.assertIn('John Doe', html_content)
        self.assertIn(reverse('booking:teacher_dashboard'), html_content)

    def test_student_email_uses_teacher_branding_and_headline(self):
        """Verify emails dynamically brand to any teacher without hardcoded names."""
        self.teacher_user.first_name = 'Alex'
        self.teacher_user.last_name = 'Taylor'
        self.teacher_user.save()
        self.teacher.headline = 'Business English & Exam Prep Coach'
        self.teacher.save()

        send_booking_request_student_email_task(self.booking.id)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn('Lesson Request Received – Alex Taylor', email.subject)
        self.assertIn('Alex Taylor', email.body)
        self.assertNotIn('English with Mary', email.body)

        html_content = email.alternatives[0][0]
        self.assertIn('Alex Taylor', html_content)
        self.assertIn('Business English &amp; Exam Prep Coach', html_content)
        self.assertNotIn('English with Mary', html_content)

    def test_missing_student_email_skips_silently(self):
        self.student_user.email = ''
        self.student_user.save()

        send_booking_request_student_email_task(self.booking.id)
        self.assertEqual(len(mail.outbox), 0)

    def test_missing_teacher_email_skips_silently(self):
        self.teacher_user.email = ''
        self.teacher_user.save()
        self.teacher.contact_email = ''
        self.teacher.save()

        send_booking_request_teacher_email_task(self.booking.id)
        self.assertEqual(len(mail.outbox), 0)

    def test_teacher_contact_email_fallback(self):
        self.teacher_user.email = ''
        self.teacher_user.save()
        self.teacher.contact_email = 'contact@englishwithmary.ir'
        self.teacher.save()

        send_booking_request_teacher_email_task(self.booking.id)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['contact@englishwithmary.ir'])

    def test_nonexistent_booking_id_skips_silently(self):
        send_booking_request_student_email_task(999999)
        send_booking_request_teacher_email_task(999999)
        self.assertEqual(len(mail.outbox), 0)

    def test_task_retries_on_transient_email_exception(self):
        with patch('booking.emails.send_booking_request_student_email', side_effect=smtplib.SMTPException('SMTP error')):
            with self.assertRaises(Exception):
                send_booking_request_student_email_task.apply(args=[self.booking.id], throw=True)

    def test_task_does_not_retry_on_non_transient_exception(self):
        with patch('booking.emails.send_booking_request_student_email', side_effect=ValueError('Programming error')):
            with self.assertRaises(ValueError):
                send_booking_request_student_email_task.apply(args=[self.booking.id], throw=True)


class BookSlotViewNotificationTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.lesson_price = 20
        cls.teacher.lesson_duration_minutes = 60
        cls.teacher.offers_trial = True
        cls.teacher.trial_price = 5
        cls.teacher.trial_duration_minutes = 30
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
        self.future_day = timezone.localdate() + timedelta(days=2)
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=self.future_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        self.valid_start = timezone.datetime.combine(
            self.future_day, time(10, 0), tzinfo=self.teacher_tz
        )

    def test_book_slot_dispatches_notifications_on_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.student_client.post(
                reverse('booking:book_slot'),
                {'start_at': self.valid_start.isoformat()},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Booking.objects.count(), 1)
        booking = Booking.objects.first()
        self.assertEqual(len(mail.outbox), 2)

        recipients = {email.to[0] for email in mail.outbox}
        self.assertEqual(recipients, {'student@example.com', 'teacher@englishwithmary.ir'})

        student_local_start = booking.student_local_start
        student_local_end = booking.student_local_end
        student_date_str = student_local_start.strftime('%A, %B %d, %Y')
        student_time_str = f"{student_local_start.strftime('%H:%M')} – {student_local_end.strftime('%H:%M')}"

        student_email = next(e for e in mail.outbox if e.to == ['student@example.com'])
        self.assertIn('Lesson Request Received', student_email.subject)
        self.assertIn(student_date_str, student_email.body)
        self.assertIn(student_time_str, student_email.body)
        self.assertIn('America/New_York', student_email.body)
        self.assertEqual(len(student_email.alternatives), 1)
        self.assertIn(student_date_str, student_email.alternatives[0][0])
        self.assertIn(student_time_str, student_email.alternatives[0][0])

        teacher_local_start = booking.teacher_local_start
        teacher_local_end = booking.teacher_local_end
        teacher_date_str = teacher_local_start.strftime('%A, %B %d, %Y')
        teacher_time_str = f"{teacher_local_start.strftime('%H:%M')} – {teacher_local_end.strftime('%H:%M')}"

        teacher_email = next(e for e in mail.outbox if e.to == ['teacher@englishwithmary.ir'])
        self.assertIn('New Lesson Request', teacher_email.subject)
        self.assertIn(teacher_date_str, teacher_email.body)
        self.assertIn(teacher_time_str, teacher_email.body)
        self.assertIn('Asia/Tehran', teacher_email.body)
        self.assertIn(reverse('booking:teacher_dashboard'), teacher_email.body)
        self.assertEqual(len(teacher_email.alternatives), 1)
        self.assertIn(teacher_date_str, teacher_email.alternatives[0][0])
        self.assertIn(teacher_time_str, teacher_email.alternatives[0][0])

    def test_failed_booking_does_not_send_notifications(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.student_client.post(
                reverse('booking:book_slot'),
                {'start_at': 'invalid-date'},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)
