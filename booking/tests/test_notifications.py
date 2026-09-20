import smtplib
from datetime import date, time, timedelta, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from booking.models import Booking, RegularAvailability
from booking.tasks import (
    send_booking_confirmed_student_email_task,
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

    def test_send_booking_confirmed_student_email_with_meeting_link(self):
        self.teacher.meeting_link = 'https://meet.google.com/abc-defg-hij'
        self.teacher.save()

        send_booking_confirmed_student_email_task(self.booking.id)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['student@example.com'])
        self.assertIn('Lesson Confirmed', email.subject)
        self.assertIn('Mary Smith', email.subject)

        # Check localized time in student's timezone (America/New_York)
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
        self.assertIn('https://meet.google.com/abc-defg-hij', email.body)

        # Check HTML alternative
        self.assertEqual(len(email.alternatives), 1)
        html_content, mimetype = email.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertIn(expected_date_str, html_content)
        self.assertIn(expected_time_str, html_content)
        self.assertIn('America/New_York', html_content)
        self.assertIn('https://meet.google.com/abc-defg-hij', html_content)
        self.assertIn('Join Lesson', html_content)

        # Check .ics attachment
        self.assertEqual(len(email.attachments), 1)
        filename, ics_content, mime = email.attachments[0]
        self.assertTrue(filename.endswith('.ics'))
        self.assertEqual(mime, 'text/calendar')

        # Check RFC 5545 compliance & structure
        self.assertIn('BEGIN:VCALENDAR', ics_content)
        self.assertIn('VERSION:2.0', ics_content)
        self.assertIn('METHOD:PUBLISH', ics_content)
        self.assertIn('BEGIN:VEVENT', ics_content)
        self.assertIn('STATUS:CONFIRMED', ics_content)

        # UTC timestamps ending in Z
        utc_start_str = self.start_at.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        utc_end_str = self.end_at.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        self.assertIn(f'DTSTART:{utc_start_str}', ics_content)
        self.assertIn(f'DTEND:{utc_end_str}', ics_content)
        self.assertIn('DTSTAMP:', ics_content)
        self.assertIn('Z', ics_content)

        # Summary, Description, Location
        self.assertIn('SUMMARY:Trial Lesson with Mary Smith', ics_content)
        self.assertIn('DESCRIPTION:', ics_content)
        self.assertIn('LOCATION:https://meet.google.com/abc-defg-hij', ics_content)
        self.assertIn(f'UID:booking-{self.booking.id}@', ics_content)
        self.assertIn('END:VEVENT', ics_content)
        self.assertIn('END:VCALENDAR', ics_content)

        # CRLF line endings
        self.assertIn('\r\n', ics_content)

    def test_send_booking_confirmed_student_email_without_meeting_link(self):
        self.teacher.meeting_link = ''
        self.teacher.save()

        send_booking_confirmed_student_email_task(self.booking.id)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['student@example.com'])
        self.assertIn('Lesson Confirmed', email.subject)

        # Fallback guidance when meeting link is not configured
        self.assertIn('share the meeting link with you prior to the lesson', email.body)
        html_content = email.alternatives[0][0]
        self.assertIn('share the meeting link with you prior to the lesson', html_content)
        self.assertNotIn('href=""', html_content)

        # .ics attachment present and handles empty location gracefully
        self.assertEqual(len(email.attachments), 1)
        filename, ics_content, mime = email.attachments[0]
        self.assertTrue(filename.endswith('.ics'))
        self.assertNotIn('LOCATION:http', ics_content)

    def test_send_booking_confirmed_uses_dynamic_teacher_branding(self):
        """Verify confirmed email dynamically brands to any teacher name and headline."""
        self.teacher_user.first_name = 'Sarah'
        self.teacher_user.last_name = 'Connor'
        self.teacher_user.save()
        self.teacher.headline = 'IELTS & Advanced Pronunciation Coach'
        self.teacher.meeting_link = 'https://zoom.us/j/123456789'
        self.teacher.save()

        send_booking_confirmed_student_email_task(self.booking.id)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn('Lesson Confirmed – Sarah Connor', email.subject)
        self.assertIn('Sarah Connor', email.body)
        self.assertNotIn('English with Mary', email.body)

        html_content = email.alternatives[0][0]
        self.assertIn('Sarah Connor', html_content)
        self.assertIn('IELTS &amp; Advanced Pronunciation Coach', html_content)
        self.assertNotIn('English with Mary', html_content)

        # .ics check
        filename, ics_content, _ = email.attachments[0]
        self.assertIn('SUMMARY:Trial Lesson with Sarah Connor', ics_content)
        self.assertIn('LOCATION:https://zoom.us/j/123456789', ics_content)

    def test_ics_line_folding_and_special_character_escaping(self):
        """Verify RFC 5545 line folding and text escaping for long descriptions/URLs."""
        from booking.emails import _fold_ics_line, _escape_ics_text

        # Escaping
        raw = r"Special chars: \ backslash, ; semicolon, , comma, and " + "\nnewline"
        escaped = _escape_ics_text(raw)
        self.assertIn(r"\\", escaped)
        self.assertIn(r"\;", escaped)
        self.assertIn(r"\,", escaped)
        self.assertIn(r"\n", escaped)

        # Folding
        long_line = "DESCRIPTION:" + "A" * 100
        folded = _fold_ics_line(long_line)
        lines = folded.split('\r\n')
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(len(line.encode('utf-8')), 75)
        # Unfolded content must match original
        unfolded = folded.replace('\r\n ', '')
        self.assertEqual(unfolded, long_line)

    def test_send_booking_confirmed_missing_student_email_skips(self):
        self.student_user.email = ''
        self.student_user.save()

        send_booking_confirmed_student_email_task(self.booking.id)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_booking_confirmed_nonexistent_booking_skips(self):
        send_booking_confirmed_student_email_task(999999)
        self.assertEqual(len(mail.outbox), 0)

    def test_confirmed_task_retries_on_transient_error(self):
        with patch('booking.emails.send_booking_confirmed_student_email', side_effect=smtplib.SMTPException('SMTP error')):
            with self.assertRaises(Exception):
                send_booking_confirmed_student_email_task.apply(args=[self.booking.id], throw=True)

    def test_confirmed_task_does_not_retry_on_non_transient_error(self):
        with patch('booking.emails.send_booking_confirmed_student_email', side_effect=ValueError('Programming error')):
            with self.assertRaises(ValueError):
                send_booking_confirmed_student_email_task.apply(args=[self.booking.id], throw=True)


class RespondToBookingNotificationTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.lesson_price = 25
        cls.teacher.lesson_duration_minutes = 60
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
        self.student_tz = ZoneInfo(self.student_user.timezone)
        future_date = timezone.localdate() + timedelta(days=3)
        self.start_at = timezone.datetime.combine(
            future_date, time(15, 0), tzinfo=ZoneInfo('UTC')
        )
        self.end_at = self.start_at + timedelta(minutes=60)

        self.booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=self.start_at,
            end_at=self.end_at,
            lesson_type=Booking.LessonType.REGULAR,
            price=self.teacher.lesson_price,
            status=Booking.Status.PENDING,
        )

    def test_respond_to_booking_accept_dispatches_confirmation_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(
                reverse('booking:respond_to_booking', kwargs={'booking_id': self.booking.id}),
                {'action': 'accept'},
            )

        self.assertEqual(response.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CONFIRMED)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['student@example.com'])
        self.assertIn('Lesson Confirmed', email.subject)
        self.assertIn('Mary Smith', email.subject)

        # Localized time check
        local_start = timezone.localtime(self.start_at, self.student_tz)
        local_end = timezone.localtime(self.end_at, self.student_tz)
        expected_time_str = f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}"
        expected_date_str = local_start.strftime('%A, %B %d, %Y')
        self.assertIn(expected_date_str, email.body)
        self.assertIn(expected_time_str, email.body)
        self.assertIn('America/New_York', email.body)
        self.assertIn('https://meet.google.com/test-room', email.body)

        # Attachment check
        self.assertEqual(len(email.attachments), 1)
        filename, ics_content, mime = email.attachments[0]
        self.assertTrue(filename.endswith('.ics'))
        self.assertEqual(mime, 'text/calendar')
        self.assertIn('STATUS:CONFIRMED', ics_content)
        self.assertIn('LOCATION:https://meet.google.com/test-room', ics_content)

    def test_respond_to_booking_decline_does_not_dispatch_confirmation_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(
                reverse('booking:respond_to_booking', kwargs={'booking_id': self.booking.id}),
                {'action': 'decline'},
            )

        self.assertEqual(response.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CANCELLED)
        self.assertEqual(len(mail.outbox), 0)

    def test_respond_to_cancellation_accept_does_not_dispatch_confirmation_email(self):
        self.booking.status = Booking.Status.CONFIRMED
        self.booking.cancellation_requested = True
        self.booking.save()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(
                reverse('booking:respond_to_booking', kwargs={'booking_id': self.booking.id}),
                {'action': 'accept'},
            )

        self.assertEqual(response.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CANCELLED)
        self.assertFalse(self.booking.cancellation_requested)
        self.assertEqual(len(mail.outbox), 0)

    def test_respond_to_expired_booking_does_not_dispatch_confirmation_email(self):
        # Create a stale booking in the past
        past_date = timezone.localdate() - timedelta(days=1)
        stale_booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=timezone.datetime.combine(past_date, time(10, 0), tzinfo=ZoneInfo('UTC')),
            end_at=timezone.datetime.combine(past_date, time(11, 0), tzinfo=ZoneInfo('UTC')),
            lesson_type=Booking.LessonType.REGULAR,
            price=self.teacher.lesson_price,
            status=Booking.Status.PENDING,
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(
                reverse('booking:respond_to_booking', kwargs={'booking_id': stale_booking.id}),
                {'action': 'accept'},
            )

        self.assertEqual(response.status_code, 400)
        stale_booking.refresh_from_db()
        self.assertEqual(stale_booking.status, Booking.Status.EXPIRED)
        self.assertEqual(len(mail.outbox), 0)


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
