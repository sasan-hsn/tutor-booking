import logging
from decimal import Decimal

from django.test import TestCase

from accounts.models import Tenant, User
from wallet.models import LedgerEntry
from wallet.services import (
    audit_wallet,
    compute_expected_balances,
    credit_wallet,
    get_or_create_wallet,
    reconcile_wallet_balances,
    repair_wallet_cache,
    reserve_funds,
)


class AuditServicesTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_audit",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)
        self.student = User.objects.create_user(
            username="student_audit",
            password="testpassword",
            role=User.Role.STUDENT,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student)

    def test_compute_expected_balances_empty_ledger(self):
        breakdown = compute_expected_balances(self.wallet)
        self.assertEqual(breakdown.expected_settled, Decimal("0.00"))
        self.assertEqual(breakdown.expected_reserved, Decimal("0.00"))

    def test_compute_expected_balances_with_various_entry_types(self):
        # 1. CREDIT: +settled 100
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="audit-credit-1",
            reference_id="ref-1",
            description="Initial top-up",
        )
        # 2. RESERVE: +reserved 30
        reserve_funds(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("30.00"),
            idempotency_key="audit-reserve-1",
            reference_id="ref-2",
            description="Lesson reserve",
        )
        # 3. Create CONSUME entry: -settled 30, -reserved 30
        LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=self.wallet,
            entry_type=LedgerEntry.EntryType.CONSUME,
            amount=Decimal("30.00"),
            settled_after=Decimal("70.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="audit-consume-1",
            reference_id="ref-3",
            description="Lesson complete",
        )
        # 4. Create WAIVER entry: +settled 15
        LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=self.wallet,
            entry_type=LedgerEntry.EntryType.WAIVER,
            amount=Decimal("15.00"),
            settled_after=Decimal("85.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="audit-waiver-1",
            reference_id="ref-4",
            description="Goodwill waiver",
        )
        # 5. Create PAYOUT_HOLD entry: +reserved 20
        LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=self.wallet,
            entry_type=LedgerEntry.EntryType.PAYOUT_HOLD,
            amount=Decimal("20.00"),
            settled_after=Decimal("85.00"),
            reserved_after=Decimal("20.00"),
            idempotency_key="audit-payout-hold-1",
            reference_id="ref-5",
            description="Payout hold",
        )
        # 6. Create PAYOUT_RELEASE entry: -reserved 20
        LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=self.wallet,
            entry_type=LedgerEntry.EntryType.PAYOUT_RELEASE,
            amount=Decimal("20.00"),
            settled_after=Decimal("85.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="audit-payout-release-1",
            reference_id="ref-6",
            description="Payout release",
        )
        # 7. Create ADJUSTMENT_CREDIT: +settled 10
        LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=self.wallet,
            entry_type=LedgerEntry.EntryType.ADJUSTMENT_CREDIT,
            amount=Decimal("10.00"),
            settled_after=Decimal("95.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="audit-adj-credit-1",
            reference_id="ref-7",
            description="Admin adjustment credit",
        )
        # 8. Create ADJUSTMENT_DEBIT: -settled 5
        LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=self.wallet,
            entry_type=LedgerEntry.EntryType.ADJUSTMENT_DEBIT,
            amount=Decimal("5.00"),
            settled_after=Decimal("90.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="audit-adj-debit-1",
            reference_id="ref-8",
            description="Admin adjustment debit",
        )

        breakdown = compute_expected_balances(self.wallet)
        # expected_settled = 100 - 30 + 15 + 10 - 5 = 90.00
        # expected_reserved = 30 - 30 + 20 - 20 = 0.00
        self.assertEqual(breakdown.expected_settled, Decimal("90.00"))
        self.assertEqual(breakdown.expected_reserved, Decimal("0.00"))

    def test_audit_wallet_detects_mismatch(self):
        # Create credit 50
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="audit-credit-mismatch",
            reference_id="ref-mismatch",
            description="Top up",
        )

        # Artificially modify cached snapshot directly in DB
        self.wallet.settled_balance = Decimal("75.00")
        self.wallet.save(update_fields=["settled_balance"])

        result = audit_wallet(wallet=self.wallet)
        self.assertTrue(result["has_discrepancy"])
        self.assertEqual(result["cached_settled"], Decimal("75.00"))
        self.assertEqual(result["expected_settled"], Decimal("50.00"))
        self.assertEqual(result["settled_discrepancy"], Decimal("25.00"))
        self.assertEqual(result["reserved_discrepancy"], Decimal("0.00"))

    def test_reconcile_wallet_balances_no_discrepancy(self):
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="reconcile-ok-1",
            reference_id="ref-ok",
            description="Top up",
        )
        discrepancies = reconcile_wallet_balances(tenant=self.tenant)
        self.assertEqual(len(discrepancies), 0)

    def test_reconcile_wallet_balances_with_discrepancy_logs_critical(self):
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="reconcile-disc-1",
            reference_id="ref-disc",
            description="Top up",
        )
        # Artificially alter cached balance
        self.wallet.settled_balance = Decimal("80.00")
        self.wallet.save(update_fields=["settled_balance"])

        with self.assertLogs("wallet.services", level=logging.CRITICAL) as log_context:
            discrepancies = reconcile_wallet_balances(tenant=self.tenant)

        self.assertEqual(len(discrepancies), 1)
        self.assertEqual(discrepancies[0]["wallet_id"], self.wallet.id)
        self.assertEqual(discrepancies[0]["cached_settled"], Decimal("80.00"))
        self.assertEqual(discrepancies[0]["expected_settled"], Decimal("50.00"))
        self.assertIn("discrepancy detected", "\n".join(log_context.output).lower())

    def test_repair_wallet_cache_dry_run_does_not_mutate(self):
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="repair-dry-1",
            reference_id="ref-dry",
            description="Top up",
        )
        self.wallet.settled_balance = Decimal("80.00")
        self.wallet.save(update_fields=["settled_balance"])

        result = repair_wallet_cache(wallet_id=self.wallet.id, commit=False)
        self.assertFalse(result["committed"])
        self.assertTrue(result["has_discrepancy"])
        self.assertEqual(result["settled_before"], Decimal("80.00"))
        self.assertEqual(result["expected_settled"], Decimal("50.00"))

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("80.00"))

    def test_repair_wallet_cache_commit_restores_balances_and_logs(self):
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="repair-commit-1",
            reference_id="ref-commit",
            description="Top up",
        )
        self.wallet.settled_balance = Decimal("80.00")
        self.wallet.save(update_fields=["settled_balance"])

        admin_user = User.objects.create_superuser(
            username="admin_repair",
            password="testpassword",
            email="admin_repair@test.local",
        )

        with self.assertLogs("wallet.services", level=logging.INFO) as log_context:
            result = repair_wallet_cache(
                wallet_id=self.wallet.id,
                admin_user=admin_user,
                commit=True,
            )

        self.assertTrue(result["committed"])
        self.assertTrue(result["has_discrepancy"])
        self.assertEqual(result["settled_after"], Decimal("50.00"))

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("50.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

        log_output = "\n".join(log_context.output)
        self.assertIn("repaired", log_output.lower())
        self.assertIn(str(admin_user.id), log_output)
