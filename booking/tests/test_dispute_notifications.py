from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.db import transaction
from django.test import override_settings
from django.utils import timezone

from accounts.models import Tenant
from accounts.tests.base import RoleTestCase
from booking.models import Booking
from booking.services import open_dispute, resolve_dispute
from booking.tasks import dispute_warning_task, expire_disputes_task
from notifications.models import Notification
from wallet.services import credit_wallet, get_or_create_wallet


class _SimulatedRollback(Exception):
    pass


@override_settings(SITE_URL="https://tutor.example.com", SITE_NAME="English with Mary")
class DisputeEmailsAndNotificationsTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

    def setUp(self):
        super().setUp()
        cache.clear()
        mail.outbox.clear()
        self.wallet = get_or_create_wallet(
            tenant=self.tenant,
            user=self.student_user,
            currency="USD",
        )
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="dispute-notif-seed-100",
            reference_id="seed-dispute-100",
            description="Seed funds",
        )

    def _create_reserved_booking(self, *, end_at, amount=Decimal("25.00")):
        from wallet.services import reserve_funds

        booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=end_at - timedelta(minutes=50),
            end_at=end_at,
            lesson_type=Booking.LessonType.REGULAR,
            price=amount,
            status=Booking.Status.CONFIRMED,
            payment_status=Booking.PaymentStatus.RESERVED,
            held_amount=amount,
        )
        reserve_funds(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=amount,
            idempotency_key=f"reserve-dispute-test-{booking.id}",
            reference_id=f"booking_{booking.id}",
            description=f"Reserve for booking #{booking.id}",
        )
        return booking

    def test_open_dispute_sends_branded_teacher_email_and_notification_on_commit(self):
        end_at = timezone.now() - timedelta(hours=2)
        booking = self._create_reserved_booking(end_at=end_at)
        xss_reason = '<script>alert("dispute")</script> Teacher never joined'

        with self.captureOnCommitCallbacks(execute=True):
            open_dispute(
                booking=booking,
                student=self.student_user,
                reason=xss_reason,
                now=end_at + timedelta(hours=1),
            )

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.teacher_user.email])
        self.assertEqual(len(sent.alternatives), 1)
        html = sent.alternatives[0][0]
        self.assertIn("English with Mary", html)
        self.assertIn(f"#{booking.id}", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

        notif = Notification.objects.get(
            recipient=self.teacher_user,
            event_type="dispute_opened",
        )
        self.assertEqual(notif.metadata.get("booking_id"), booking.id)

    def test_open_dispute_rollback_emits_no_email_or_notification(self):
        end_at = timezone.now() - timedelta(hours=2)
        booking = self._create_reserved_booking(end_at=end_at)

        with self.assertRaises(_SimulatedRollback):
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    open_dispute(
                        booking=booking,
                        student=self.student_user,
                        reason="Connection failure",
                        now=end_at + timedelta(hours=1),
                    )
                    raise _SimulatedRollback("rollback dispute")

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_dispute_warning_emails_render_branded_html_for_day3_and_day6(self):
        now = timezone.now()
        booking_day3 = self._create_reserved_booking(end_at=now - timedelta(days=4))
        open_dispute(
            booking=booking_day3,
            student=self.student_user,
            reason="Audio dropped",
            now=now - timedelta(days=3, hours=2),
        )
        booking_day6 = self._create_reserved_booking(end_at=now - timedelta(days=7))
        open_dispute(
            booking=booking_day6,
            student=self.student_user,
            reason="Video froze",
            now=now - timedelta(days=6, hours=3),
        )
        mail.outbox.clear()

        sent_count = dispute_warning_task(now=now)
        self.assertEqual(sent_count, 2)
        self.assertEqual(len(mail.outbox), 2)

        day3_email = next(m for m in mail.outbox if f"#{booking_day3.id}" in m.subject)
        self.assertEqual(len(day3_email.alternatives), 1)
        self.assertIn("Day 3", day3_email.alternatives[0][0])
        self.assertIn("4 days", day3_email.alternatives[0][0])

        day6_email = next(m for m in mail.outbox if f"#{booking_day6.id}" in m.subject)
        self.assertEqual(len(day6_email.alternatives), 1)
        self.assertIn("Day 6", day6_email.alternatives[0][0])
        self.assertIn("1 day", day6_email.alternatives[0][0])

    def test_dispute_resolved_transient_failure_retries_only_failed_recipient(self):
        import smtplib

        from booking.tasks import send_dispute_resolved_emails_task

        end_at = timezone.now() - timedelta(hours=3)
        booking = self._create_reserved_booking(end_at=end_at)
        open_dispute(
            booking=booking,
            student=self.student_user,
            reason="Connection drop",
            now=end_at + timedelta(hours=1),
        )
        mail.outbox.clear()

        call_count = 0
        original_send = mail.EmailMultiAlternatives.send

        def flaky_send(msg_self, fail_silently=False):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise smtplib.SMTPException("Temporary SMTP error")
            return original_send(msg_self, fail_silently=fail_silently)

        with patch.object(
            mail.EmailMultiAlternatives, "send", autospec=True, side_effect=flaky_send
        ):
            from celery.exceptions import Retry

            with self.assertRaises(Retry):
                send_dispute_resolved_emails_task.apply(
                    args=[booking.id, Booking.DisputeResolution.STUDENT_FAVOR],
                    throw=True,
                )
            self.assertEqual(len(mail.outbox), 1)
            # Simulated Celery retry sends only to the failed recipient
            send_dispute_resolved_emails_task(
                booking.id, Booking.DisputeResolution.STUDENT_FAVOR
            )

        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(call_count, 3)

    def test_dispute_resolved_sends_branded_html_to_both_with_differentiated_dedup(
        self,
    ):
        end_at = timezone.now() - timedelta(hours=3)
        booking = self._create_reserved_booking(end_at=end_at)
        open_dispute(
            booking=booking,
            student=self.student_user,
            reason="Audio issues",
            now=end_at + timedelta(hours=1),
        )
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            resolve_dispute(
                tenant=self.tenant,
                booking=booking,
                teacher_user=self.teacher_user,
                resolution=Booking.DisputeResolution.STUDENT_FAVOR,
            )

        self.assertEqual(len(mail.outbox), 2)
        for msg in mail.outbox:
            self.assertEqual(len(msg.alternatives), 1)
            self.assertIn("Resolved in Student Favor", msg.alternatives[0][0])
            self.assertIn("$25.00", msg.alternatives[0][0])

        recipients = {tuple(m.to) for m in mail.outbox}
        self.assertEqual(
            recipients,
            {(self.student_user.email,), (self.teacher_user.email,)},
        )

        # Duplicate invocation is suppressed for both recipients
        from booking.emails import send_dispute_resolved_emails

        self.assertEqual(
            send_dispute_resolved_emails(
                booking, resolution=Booking.DisputeResolution.STUDENT_FAVOR
            ),
            0,
        )

    def test_expire_disputes_task_sends_auto_expired_html_emails_and_notifications(
        self,
    ):
        now = timezone.now()
        booking = self._create_reserved_booking(end_at=now - timedelta(days=8))
        open_dispute(
            booking=booking,
            student=self.student_user,
            reason="Unanswered dispute",
            now=now - timedelta(days=7, hours=1),
        )
        Notification.objects.all().delete()
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            expired_count = expire_disputes_task(now=now)

        self.assertEqual(expired_count, 1)
        self.assertEqual(len(mail.outbox), 2)
        recipients = {tuple(m.to) for m in mail.outbox}
        self.assertEqual(
            recipients,
            {(self.student_user.email,), (self.teacher_user.email,)},
        )
        for msg in mail.outbox:
            self.assertEqual(len(msg.alternatives), 1)
            self.assertIn("Auto-Expired", msg.alternatives[0][0])
            self.assertIn("$25.00", msg.alternatives[0][0])

        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.DISPUTE_RESOLVED,
                metadata__booking_id=booking.id,
            ).count(),
            2,
        )
