import logging
from decimal import Decimal

from django.test import TestCase

from accounts.models import Tenant, User
from wallet.exceptions import (
    InsufficientBalanceError,
    InvalidAmountError,
    TenantMismatchError,
)
from wallet.models import LedgerEntry
from wallet.services import adjust_wallet, credit_wallet, get_or_create_wallet


class AdjustWalletTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_adj",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)

        self.other_teacher = User.objects.create_user(
            username="teacher_adj_other",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.other_tenant = Tenant.objects.get(
            teacher_profile=self.other_teacher.teacher_profile
        )

        self.student = User.objects.create_user(
            username="student_adj",
            password="testpassword",
            role=User.Role.STUDENT,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student)

        self.admin_user = User.objects.create_superuser(
            username="admin_adj",
            password="testpassword",
            email="admin@test.local",
        )

    def test_empty_or_whitespace_reason_raises_value_error(self):
        for invalid_reason in ["", "   ", "\t\n"]:
            with self.subTest(reason=invalid_reason):
                with self.assertRaises(ValueError):
                    adjust_wallet(
                        tenant=self.tenant,
                        wallet=self.wallet,
                        amount=Decimal("10.00"),
                        direction="credit",
                        reason=invalid_reason,
                        admin_user=self.admin_user,
                    )

    def test_invalid_direction_raises_value_error(self):
        for invalid_dir in ["refund", "add", "subtract", ""]:
            with self.subTest(direction=invalid_dir):
                with self.assertRaises(ValueError):
                    adjust_wallet(
                        tenant=self.tenant,
                        wallet=self.wallet,
                        amount=Decimal("10.00"),
                        direction=invalid_dir,
                        reason="Test justification",
                        admin_user=self.admin_user,
                    )

    def test_non_positive_amount_raises_invalid_amount_error(self):
        for invalid_amt in [Decimal("0.00"), Decimal("-10.00")]:
            with self.subTest(amount=invalid_amt):
                with self.assertRaises(InvalidAmountError):
                    adjust_wallet(
                        tenant=self.tenant,
                        wallet=self.wallet,
                        amount=invalid_amt,
                        direction="credit",
                        reason="Test adjustment",
                        admin_user=self.admin_user,
                    )

    def test_tenant_mismatch_raises_error(self):
        with self.assertRaises(TenantMismatchError):
            adjust_wallet(
                tenant=self.other_tenant,
                wallet=self.wallet,
                amount=Decimal("10.00"),
                direction="credit",
                reason="Cross-tenant test",
                admin_user=self.admin_user,
            )

    def test_adjust_wallet_credit_success(self):
        with self.assertLogs("wallet.services", level=logging.INFO) as log_context:
            entry = adjust_wallet(
                tenant=self.tenant,
                wallet=self.wallet,
                amount=Decimal("50.00"),
                direction="credit",
                reason="Goodwill correction for dispute #123",
                admin_user=self.admin_user,
            )

        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.ADJUSTMENT_CREDIT)
        self.assertEqual(entry.amount, Decimal("50.00"))
        self.assertEqual(entry.settled_after, Decimal("50.00"))
        self.assertEqual(entry.reserved_after, Decimal("0.00"))
        self.assertEqual(self.wallet.settled_balance, Decimal("50.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("50.00"))

        # Verify audit log was emitted
        log_output = "\n".join(log_context.output)
        self.assertIn(str(self.admin_user.id), log_output)
        self.assertIn("Goodwill correction for dispute #123", log_output)
        self.assertIn("credit", log_output)

    def test_adjust_wallet_debit_success(self):
        # First credit 100 so there are available funds
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="adj-test-init-100",
            reference_id="ref-init",
            description="Initial balance",
        )

        with self.assertLogs("wallet.services", level=logging.INFO) as log_context:
            entry = adjust_wallet(
                tenant=self.tenant,
                wallet=self.wallet,
                amount=Decimal("30.00"),
                direction="debit",
                reason="Clawback of duplicate deposit",
                admin_user=self.admin_user,
            )

        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.ADJUSTMENT_DEBIT)
        self.assertEqual(entry.amount, Decimal("30.00"))
        self.assertEqual(entry.settled_after, Decimal("70.00"))
        self.assertEqual(entry.reserved_after, Decimal("0.00"))
        self.assertEqual(self.wallet.settled_balance, Decimal("70.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("70.00"))

        # Verify audit log was emitted
        log_output = "\n".join(log_context.output)
        self.assertIn(str(self.admin_user.id), log_output)
        self.assertIn("Clawback of duplicate deposit", log_output)
        self.assertIn("debit", log_output)

    def test_adjust_wallet_debit_insufficient_balance_raises_error(self):
        # Wallet has 0 available balance
        with self.assertRaises(InsufficientBalanceError):
            adjust_wallet(
                tenant=self.tenant,
                wallet=self.wallet,
                amount=Decimal("20.00"),
                direction="debit",
                reason="Attempted debit on empty wallet",
                admin_user=self.admin_user,
            )
