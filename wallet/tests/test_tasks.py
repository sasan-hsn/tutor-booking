import logging
from decimal import Decimal

from django.conf import settings
from django.test import TestCase

from accounts.models import Tenant, User
from wallet.services import credit_wallet, get_or_create_wallet
from wallet.tasks import reconcile_wallet_balances_task


class ReconcileWalletBalancesTaskTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_task",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)
        self.student = User.objects.create_user(
            username="student_task",
            password="testpassword",
            role=User.Role.STUDENT,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student)

    def test_reconcile_task_no_discrepancy(self):
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="task-clean-1",
            reference_id="ref-task-clean",
            description="Clean balance",
        )
        count = reconcile_wallet_balances_task()
        self.assertEqual(count, 0)

    def test_reconcile_task_flags_mismatched_wallet_and_logs_critical(self):
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="task-disc-1",
            reference_id="ref-task-disc",
            description="Initial balance",
        )
        # Artificially alter cached balance
        self.wallet.settled_balance = Decimal("150.00")
        self.wallet.save(update_fields=["settled_balance"])

        with self.assertLogs("wallet.tasks", level=logging.CRITICAL) as log_context:
            count = reconcile_wallet_balances_task()

        self.assertEqual(count, 1)
        log_output = "\n".join(log_context.output)
        self.assertIn("discrepancies detected", log_output.lower())
        self.assertIn(str(self.wallet.id), log_output)

    def test_beat_schedule_registration(self):
        beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
        self.assertIn("reconcile-wallet-balances-daily", beat_schedule)
        entry = beat_schedule["reconcile-wallet-balances-daily"]
        self.assertEqual(entry["task"], "wallet.tasks.reconcile_wallet_balances_task")
