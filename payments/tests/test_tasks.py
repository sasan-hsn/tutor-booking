import uuid
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from accounts.models import Tenant, User
from payments.models import Order, Payout
from payments.services import create_payout_request
from payments.tasks import (
    expire_orders_task,
    expire_payout_tokens_task,
    payout_reminder_task,
)
from wallet.services import credit_wallet, get_or_create_wallet


class PeriodicExpirationTasksTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_tasks_test",
            password="testpassword",
            role=User.Role.TEACHER,
            email="teacher_task@example.com",
            first_name="Teacher",
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)

        self.student = User.objects.create_user(
            username="student_tasks_test",
            password="testpassword",
            role=User.Role.STUDENT,
            email="student_task@example.com",
            first_name="Jane",
        )

        self.wallet = get_or_create_wallet(
            tenant=self.tenant,
            user=self.student,
            currency="USD",
        )

        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="setup-credit-tasks-100",
            reference_id="ref-tasks-100",
            description="Initial top-up for tasks tests",
        )

    def test_expire_payout_tokens_task(self):
        now = timezone.now()

        # Payout 1: Expired unconfirmed token
        payout_expired = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=Decimal("20.00"),
            destination="0x71C8000000000000000000000000000000000000",
        )
        payout_expired.confirmation_expires_at = now - timedelta(hours=1)
        payout_expired.save(update_fields=["confirmation_expires_at"])

        # Payout 2: Active unconfirmed token (future expiry)
        payout_active = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=Decimal("20.00"),
            destination="0x71C8000000000000000000000000000000000000",
        )
        payout_active.confirmation_expires_at = now + timedelta(hours=10)
        payout_active.save(update_fields=["confirmation_expires_at"])

        # Payout 3: Expired confirmation time but already confirmed (REQUESTED)
        payout_requested = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=Decimal("20.00"),
            destination="0x71C8000000000000000000000000000000000000",
        )
        payout_requested.status = Payout.Status.REQUESTED
        payout_requested.confirmation_expires_at = now - timedelta(hours=1)
        payout_requested.save(update_fields=["status", "confirmation_expires_at"])

        # Run task
        expired_count = expire_payout_tokens_task()

        self.assertEqual(expired_count, 1)

        payout_expired.refresh_from_db()
        self.assertEqual(payout_expired.status, Payout.Status.EXPIRED)

        payout_active.refresh_from_db()
        self.assertEqual(payout_active.status, Payout.Status.PENDING_CONFIRMATION)

        payout_requested.refresh_from_db()
        self.assertEqual(payout_requested.status, Payout.Status.REQUESTED)

    def test_expire_orders_task(self):
        now = timezone.now()

        # Order 1: Expired pending order
        order_expired = Order.objects.create(
            tenant=self.tenant,
            student=self.student,
            amount=Decimal("25.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key=f"order_{uuid.uuid4().hex}",
            expires_at=now - timedelta(hours=1),
        )

        # Order 2: Active pending order
        order_active = Order.objects.create(
            tenant=self.tenant,
            student=self.student,
            amount=Decimal("25.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key=f"order_{uuid.uuid4().hex}",
            expires_at=now + timedelta(hours=10),
        )

        # Order 3: Completed order whose expires_at is past
        order_completed = Order.objects.create(
            tenant=self.tenant,
            student=self.student,
            amount=Decimal("25.00"),
            currency="USD",
            status=Order.Status.COMPLETED,
            idempotency_key=f"order_{uuid.uuid4().hex}",
            expires_at=now - timedelta(hours=1),
        )

        expired_count = expire_orders_task()

        self.assertEqual(expired_count, 1)

        order_expired.refresh_from_db()
        self.assertEqual(order_expired.status, Order.Status.EXPIRED)

        order_active.refresh_from_db()
        self.assertEqual(order_active.status, Order.Status.PENDING)

        order_completed.refresh_from_db()
        self.assertEqual(order_completed.status, Order.Status.COMPLETED)

    def test_payout_reminder_task(self):
        now = timezone.now()

        # Payout 1: In REQUESTED status for > 48 hours without reminder sent
        payout_due = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=Decimal("25.00"),
            destination="0x71C8000000000000000000000000000000000000",
        )
        payout_due.status = Payout.Status.REQUESTED
        payout_due.confirmation_used_at = now - timedelta(hours=49)
        payout_due.reminder_48h_sent_at = None
        payout_due.save(
            update_fields=["status", "confirmation_used_at", "reminder_48h_sent_at"]
        )

        # Payout 2: In REQUESTED status for only 24 hours (< 48 hours)
        payout_recent = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=Decimal("25.00"),
            destination="0x71C8000000000000000000000000000000000000",
        )
        payout_recent.status = Payout.Status.REQUESTED
        payout_recent.confirmation_used_at = now - timedelta(hours=24)
        payout_recent.reminder_48h_sent_at = None
        payout_recent.save(
            update_fields=["status", "confirmation_used_at", "reminder_48h_sent_at"]
        )

        # Payout 3: In REQUESTED status for 50 hours, but reminder already sent
        payout_reminded = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=Decimal("25.00"),
            destination="0x71C8000000000000000000000000000000000000",
        )
        payout_reminded.status = Payout.Status.REQUESTED
        payout_reminded.confirmation_used_at = now - timedelta(hours=50)
        payout_reminded.reminder_48h_sent_at = now - timedelta(hours=2)
        payout_reminded.save(
            update_fields=["status", "confirmation_used_at", "reminder_48h_sent_at"]
        )

        # Payout 4: In PROCESSING status for 50 hours (should not receive reminder)
        payout_processing = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=Decimal("25.00"),
            destination="0x71C8000000000000000000000000000000000000",
        )
        payout_processing.status = Payout.Status.PROCESSING
        payout_processing.confirmation_used_at = now - timedelta(hours=50)
        payout_processing.reminder_48h_sent_at = None
        payout_processing.save(
            update_fields=["status", "confirmation_used_at", "reminder_48h_sent_at"]
        )

        mail.outbox.clear()
        reminded_count = payout_reminder_task()

        self.assertEqual(reminded_count, 1)

        payout_due.refresh_from_db()
        self.assertIsNotNone(payout_due.reminder_48h_sent_at)

        payout_recent.refresh_from_db()
        self.assertIsNone(payout_recent.reminder_48h_sent_at)

        payout_processing.refresh_from_db()
        self.assertIsNone(payout_processing.reminder_48h_sent_at)

        # Email check
        self.assertEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[0]
        self.assertIn(self.teacher.email, sent_email.to)
        self.assertIn("Reminder", sent_email.subject)
        self.assertIn(str(payout_due.id), sent_email.body)
