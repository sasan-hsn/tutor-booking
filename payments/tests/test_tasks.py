import uuid
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from accounts.models import Tenant, User
from payments.models import Order, PaymentAddress, Payout
from payments.services import create_payout_request
from payments.tasks import (
    activate_pending_tenant_accounts_task,
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
        with self.captureOnCommitCallbacks(execute=True):
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

    def test_activate_pending_tenant_accounts_task_promotes_matured_and_retires_active(
        self,
    ):
        now = timezone.now()

        # Tenant 1: ACTIVE + matured PENDING_ACTIVATION
        addr_old_active = PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
            status=PaymentAddress.Status.ACTIVE,
            activates_at=now - timedelta(days=5),
        )
        addr_matured_pending = PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address="0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
            status=PaymentAddress.Status.PENDING_ACTIVATION,
            activates_at=now - timedelta(minutes=1),
        )

        # Tenant 2: ACTIVE + unmatured PENDING_ACTIVATION (must not promote yet)
        teacher2 = User.objects.create_user(
            username="teacher_tasks_test_2",
            password="testpassword",
            role=User.Role.TEACHER,
            email="teacher_task2@example.com",
        )
        tenant2 = Tenant.objects.get(teacher_profile=teacher2.teacher_profile)
        addr_t2_active = PaymentAddress.objects.create(
            tenant=tenant2,
            provider="crypto_usdt",
            address="0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
            status=PaymentAddress.Status.ACTIVE,
            activates_at=now - timedelta(days=3),
        )
        addr_t2_future = PaymentAddress.objects.create(
            tenant=tenant2,
            provider="crypto_usdt",
            address="0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
            status=PaymentAddress.Status.PENDING_ACTIVATION,
            activates_at=now + timedelta(hours=2),
        )

        # Tenant 3: RETIRED + matured PENDING_ACTIVATION (no current ACTIVE)
        teacher3 = User.objects.create_user(
            username="teacher_tasks_test_3",
            password="testpassword",
            role=User.Role.TEACHER,
            email="teacher_task3@example.com",
        )
        tenant3 = Tenant.objects.get(teacher_profile=teacher3.teacher_profile)
        addr_t3_retired = PaymentAddress.objects.create(
            tenant=tenant3,
            provider="crypto_usdt",
            address="0x1111111111111111111111111111111111111111",
            status=PaymentAddress.Status.RETIRED,
            activates_at=now - timedelta(days=10),
            retired_at=now - timedelta(days=2),
        )
        addr_t3_matured = PaymentAddress.objects.create(
            tenant=tenant3,
            provider="crypto_usdt",
            address="0x2222222222222222222222222222222222222222",
            status=PaymentAddress.Status.PENDING_ACTIVATION,
            activates_at=now,
        )

        activated_count = activate_pending_tenant_accounts_task()

        self.assertEqual(activated_count, 2)

        addr_old_active.refresh_from_db()
        self.assertEqual(addr_old_active.status, PaymentAddress.Status.RETIRED)
        self.assertIsNotNone(addr_old_active.retired_at)

        addr_matured_pending.refresh_from_db()
        self.assertEqual(addr_matured_pending.status, PaymentAddress.Status.ACTIVE)

        addr_t2_active.refresh_from_db()
        self.assertEqual(addr_t2_active.status, PaymentAddress.Status.ACTIVE)
        addr_t2_future.refresh_from_db()
        self.assertEqual(
            addr_t2_future.status, PaymentAddress.Status.PENDING_ACTIVATION
        )

        addr_t3_retired.refresh_from_db()
        self.assertEqual(addr_t3_retired.status, PaymentAddress.Status.RETIRED)
        addr_t3_matured.refresh_from_db()
        self.assertEqual(addr_t3_matured.status, PaymentAddress.Status.ACTIVE)
