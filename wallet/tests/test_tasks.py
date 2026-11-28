import logging
from decimal import Decimal

from celery.schedules import crontab
from django.conf import settings
from django.test import TestCase

from accounts.models import Tenant, User
from wallet.services import credit_wallet, get_or_create_wallet, reserve_funds
from wallet.tasks import reconcile_wallet_balances_task
from wallet.tests.sentry_helpers import install_sentry_capture


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
        captured = install_sentry_capture(self)
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
        self.assertEqual(captured, [])

    def test_reconcile_task_flags_mismatched_wallet_and_logs_critical(self):
        captured = install_sentry_capture(self)
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

        self.assertEqual(len(captured), 1)
        event = captured[0]
        self.assertEqual(event.get("level"), "fatal")
        self.assertEqual(event["tags"]["wallet_id"], str(self.wallet.id))
        self.assertEqual(event["tags"]["tenant_id"], str(self.tenant.id))
        self.assertIn("50.00", event["tags"]["discrepancy"])

        payload = event["contexts"]["wallet_reconciliation"]
        self.assertEqual(payload["wallet_id"], self.wallet.id)
        self.assertEqual(payload["user_id"], self.student.id)
        self.assertEqual(payload["tenant_id"], self.tenant.id)
        self.assertEqual(payload["cached_settled"], "150.00")
        self.assertEqual(payload["expected_settled"], "100.00")
        self.assertEqual(payload["settled_discrepancy"], "50.00")
        self.assertEqual(payload["reserved_discrepancy"], "0.00")

    def test_reconcile_task_captures_reserved_drift_in_sentry_payload(self):
        captured = install_sentry_capture(self)
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="task-res-credit-1",
            reference_id="ref-task-res-credit",
            description="Initial balance",
        )
        reserve_funds(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("25.00"),
            idempotency_key="task-res-hold-1",
            reference_id="ref-task-res-hold",
            description="Lesson hold",
        )
        # Artificially corrupt cached reserved_balance
        self.wallet.reserved_balance = Decimal("10.00")
        self.wallet.save(update_fields=["reserved_balance"])

        count = reconcile_wallet_balances_task()
        self.assertEqual(count, 1)
        self.assertEqual(len(captured), 1)

        event = captured[0]
        self.assertEqual(event["tags"]["wallet_id"], str(self.wallet.id))
        self.assertEqual(event["tags"]["tenant_id"], str(self.tenant.id))
        self.assertIn("-15.00", event["tags"]["discrepancy"])
        payload = event["contexts"]["wallet_reconciliation"]
        self.assertEqual(payload["cached_reserved"], "10.00")
        self.assertEqual(payload["expected_reserved"], "25.00")
        self.assertEqual(payload["reserved_discrepancy"], "-15.00")

    def test_beat_schedule_registration(self):
        beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
        self.assertIn("reconcile-wallet-balances-daily", beat_schedule)
        entry = beat_schedule["reconcile-wallet-balances-daily"]
        self.assertEqual(entry["task"], "wallet.tasks.reconcile_wallet_balances_task")
        self.assertEqual(entry["schedule"], crontab(hour=0, minute=0))
        self.assertEqual(settings.CELERY_TIMEZONE, "UTC")
