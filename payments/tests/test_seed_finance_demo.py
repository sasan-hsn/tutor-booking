from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from accounts.models import Tenant, User
from payments.management.commands.seed_finance_demo import (
    DEMO_COMPLETED_TXHASH,
    DEMO_STUDENTS,
)
from payments.models import Payout
from wallet.models import LedgerEntry, Wallet
from wallet.services import reconcile_wallet_balances


class SeedFinanceDemoCommandTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="qa_teacher",
            email="qa_teacher@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.tenant = Tenant.objects.get(
            teacher_profile=self.teacher_user.teacher_profile
        )

    @override_settings(DEBUG=False)
    def test_refuses_to_run_when_debug_is_false_without_force(self):
        with self.assertRaises(CommandError):
            call_command("seed_finance_demo", stdout=StringIO())

    @override_settings(DEBUG=True)
    def test_seeds_students_wallets_and_all_four_scenarios_idempotently(self):
        out = StringIO()
        call_command("seed_finance_demo", stdout=out)
        output = out.getvalue()

        self.assertIn("qa_teacher", output)
        self.assertIn("/accounts/login/", output)
        self.assertIn("/dashboard/finance/", output)
        self.assertIn(DEMO_COMPLETED_TXHASH, output)

        # 3 demo students with funded, reconciled wallets
        for spec in DEMO_STUDENTS:
            student = User.objects.get(username=spec["username"])
            wallet = Wallet.objects.get(tenant=self.tenant, user=student)
            self.assertGreater(wallet.settled_balance, 0)

        self.assertEqual(reconcile_wallet_balances(tenant=self.tenant), [])

        # All 4 scenarios exist in expected statuses
        self.assertEqual(
            Payout.objects.filter(
                tenant=self.tenant, status=Payout.Status.REQUESTED
            ).count(),
            1,
        )
        self.assertEqual(
            Payout.objects.filter(
                tenant=self.tenant, status=Payout.Status.PROCESSING
            ).count(),
            1,
        )
        self.assertEqual(
            Payout.objects.filter(
                tenant=self.tenant,
                status=Payout.Status.COMPLETED,
                external_ref=DEMO_COMPLETED_TXHASH,
            ).count(),
            1,
        )
        self.assertEqual(
            Payout.objects.filter(
                tenant=self.tenant, status=Payout.Status.PENDING_CONFIRMATION
            ).count(),
            1,
        )

        entry_count_first = LedgerEntry.objects.filter(tenant=self.tenant).count()
        payout_count_first = Payout.objects.filter(tenant=self.tenant).count()

        # Re-running the command must be idempotent
        call_command("seed_finance_demo", stdout=StringIO())
        self.assertEqual(
            LedgerEntry.objects.filter(tenant=self.tenant).count(),
            entry_count_first,
        )
        self.assertEqual(
            Payout.objects.filter(tenant=self.tenant).count(),
            payout_count_first,
        )
        self.assertEqual(reconcile_wallet_balances(tenant=self.tenant), [])
