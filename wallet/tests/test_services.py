from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Tenant, User
from wallet.exceptions import (
    InsufficientBalanceError,
    InvalidAmountError,
    InvalidEntryTypeError,
    TenantMismatchError,
    WalletFrozenError,
)
from wallet.models import LedgerEntry
from wallet.services import credit_wallet, get_or_create_wallet, reserve_funds


class WalletServicesTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_service",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)

        # Another teacher / tenant for cross-tenant isolation testing
        self.teacher2 = User.objects.create_user(
            username="teacher_other",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.other_tenant = Tenant.objects.get(
            teacher_profile=self.teacher2.teacher_profile
        )

        self.student = User.objects.create_user(
            username="student_service",
            password="testpassword",
            role=User.Role.STUDENT,
        )

    # -------------------------------------------------------------
    # get_or_create_wallet
    # -------------------------------------------------------------
    def test_get_or_create_wallet_creates_and_retrieves(self):
        wallet1 = get_or_create_wallet(self.tenant, self.student)
        self.assertIsNotNone(wallet1.pk)
        self.assertEqual(wallet1.tenant, self.tenant)
        self.assertEqual(wallet1.user, self.student)
        self.assertEqual(wallet1.currency, "USD")
        self.assertEqual(wallet1.settled_balance, Decimal("0.00"))
        self.assertEqual(wallet1.reserved_balance, Decimal("0.00"))
        self.assertFalse(wallet1.is_frozen)

        # Idempotent retrieval
        wallet2 = get_or_create_wallet(self.tenant, self.student)
        self.assertEqual(wallet1.pk, wallet2.pk)

    # -------------------------------------------------------------
    # credit_wallet
    # -------------------------------------------------------------
    def test_credit_wallet_success(self):
        wallet = get_or_create_wallet(self.tenant, self.student)
        entry = credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("50.00"),
            idempotency_key="credit_test_001",
            reference_id="ref_cred_001",
            description="Top up 50",
            entry_type=LedgerEntry.EntryType.CREDIT,
        )

        wallet.refresh_from_db()
        self.assertEqual(wallet.settled_balance, Decimal("50.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(wallet.available_balance, Decimal("50.00"))

        self.assertEqual(entry.amount, Decimal("50.00"))
        self.assertEqual(entry.settled_after, Decimal("50.00"))
        self.assertEqual(entry.reserved_after, Decimal("0.00"))
        self.assertEqual(entry.entry_type, "CREDIT")

    def test_credit_wallet_permitted_when_frozen(self):
        """Freezing semantics permit credit_wallet."""
        wallet = get_or_create_wallet(self.tenant, self.student)
        wallet.is_frozen = True
        wallet.save(update_fields=["is_frozen"])

        entry = credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("25.00"),
            idempotency_key="credit_frozen_001",
            reference_id="ref_cred_froz",
            description="Credit frozen wallet",
        )
        wallet.refresh_from_db()
        self.assertEqual(wallet.settled_balance, Decimal("25.00"))
        self.assertEqual(entry.settled_after, Decimal("25.00"))

    def test_credit_wallet_rejects_non_positive_or_non_decimal_amount(self):
        wallet = get_or_create_wallet(self.tenant, self.student)

        for invalid_amount in [
            Decimal("0.00"),
            Decimal("-10.00"),
            50,
            50.0,
            "50.00",
            None,
        ]:
            with self.subTest(amount=invalid_amount):
                with self.assertRaises(InvalidAmountError):
                    credit_wallet(
                        tenant=self.tenant,
                        wallet=wallet,
                        amount=invalid_amount,  # type: ignore[arg-type]
                        idempotency_key=f"inv_cred_{invalid_amount}",
                        reference_id="ref_inv",
                        description="Invalid amount",
                    )

    def test_credit_wallet_entry_type_whitelist(self):
        """credit_wallet accepts CREDIT, WAIVER, ADJUSTMENT_CREDIT; rejects others."""
        wallet = get_or_create_wallet(self.tenant, self.student)

        # Allowed types
        for allowed in [
            LedgerEntry.EntryType.CREDIT,
            LedgerEntry.EntryType.WAIVER,
            LedgerEntry.EntryType.ADJUSTMENT_CREDIT,
        ]:
            entry = credit_wallet(
                tenant=self.tenant,
                wallet=wallet,
                amount=Decimal("10.00"),
                idempotency_key=f"key_allowed_{allowed}",
                reference_id="ref_allowed",
                description="Allowed type",
                entry_type=allowed,
            )
            self.assertEqual(entry.entry_type, allowed)

        # Disallowed types
        for disallowed in [
            LedgerEntry.EntryType.RESERVE,
            LedgerEntry.EntryType.RELEASE,
            LedgerEntry.EntryType.CONSUME,
            LedgerEntry.EntryType.PAYOUT_HOLD,
            LedgerEntry.EntryType.PAYOUT_SETTLE,
            LedgerEntry.EntryType.PAYOUT_RELEASE,
            LedgerEntry.EntryType.ADJUSTMENT_DEBIT,
            "UNKNOWN_TYPE",
        ]:
            with self.subTest(entry_type=disallowed):
                with self.assertRaises(InvalidEntryTypeError):
                    credit_wallet(
                        tenant=self.tenant,
                        wallet=wallet,
                        amount=Decimal("10.00"),
                        idempotency_key=f"key_disallowed_{disallowed}",
                        reference_id="ref_disallowed",
                        description="Disallowed type",
                        entry_type=disallowed,
                    )

    def test_credit_wallet_tenant_isolation(self):
        """credit_wallet scoped by tenant prevents cross-tenant mutations."""
        wallet = get_or_create_wallet(self.tenant, self.student)
        with self.assertRaises(TenantMismatchError):
            credit_wallet(
                tenant=self.other_tenant,
                wallet=wallet,
                amount=Decimal("20.00"),
                idempotency_key="cross_tenant_credit",
                reference_id="ref_cross",
                description="Cross tenant attempt",
            )
        wallet.refresh_from_db()
        self.assertEqual(wallet.settled_balance, Decimal("0.00"))

    def test_credit_wallet_idempotency_key_collision_rolls_back(self):
        wallet = get_or_create_wallet(self.tenant, self.student)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("30.00"),
            idempotency_key="dup_credit_key",
            reference_id="ref_first",
            description="First attempt",
        )
        wallet.refresh_from_db()
        self.assertEqual(wallet.settled_balance, Decimal("30.00"))

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                credit_wallet(
                    tenant=self.tenant,
                    wallet=wallet,
                    amount=Decimal("30.00"),
                    idempotency_key="dup_credit_key",
                    reference_id="ref_second",
                    description="Duplicate attempt",
                )

        wallet.refresh_from_db()
        self.assertEqual(wallet.settled_balance, Decimal("30.00"))

    # -------------------------------------------------------------
    # reserve_funds
    # -------------------------------------------------------------
    def test_reserve_funds_success(self):
        wallet = get_or_create_wallet(self.tenant, self.student)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("100.00"),
            idempotency_key="credit_for_reserve",
            reference_id="ref_init",
            description="Initial credit",
        )

        entry = reserve_funds(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("30.00"),
            idempotency_key="reserve_test_001",
            reference_id="booking_001",
            description="Reserve for lesson",
        )

        wallet.refresh_from_db()
        self.assertEqual(wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("30.00"))
        self.assertEqual(wallet.available_balance, Decimal("70.00"))

        self.assertEqual(entry.amount, Decimal("30.00"))
        self.assertEqual(entry.settled_after, Decimal("100.00"))
        self.assertEqual(entry.reserved_after, Decimal("30.00"))
        self.assertEqual(entry.entry_type, "RESERVE")

    def test_reserve_funds_rejects_frozen_wallet(self):
        wallet = get_or_create_wallet(self.tenant, self.student)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("50.00"),
            idempotency_key="credit_before_freeze",
            reference_id="ref_froz",
            description="Credit",
        )
        wallet.is_frozen = True
        wallet.save(update_fields=["is_frozen"])

        with self.assertRaises(WalletFrozenError):
            reserve_funds(
                tenant=self.tenant,
                wallet=wallet,
                amount=Decimal("20.00"),
                idempotency_key="reserve_frozen_key",
                reference_id="booking_froz",
                description="Reserve frozen",
            )

        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))

    def test_reserve_funds_rejects_insufficient_available_balance(self):
        wallet = get_or_create_wallet(self.tenant, self.student)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("20.00"),
            idempotency_key="credit_insufficient",
            reference_id="ref_low",
            description="Low balance",
        )

        with self.assertRaises(InsufficientBalanceError):
            reserve_funds(
                tenant=self.tenant,
                wallet=wallet,
                amount=Decimal("25.00"),
                idempotency_key="reserve_too_much",
                reference_id="booking_too_much",
                description="Too much",
            )

        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))

    def test_reserve_funds_rejects_non_positive_or_non_decimal_amount(self):
        wallet = get_or_create_wallet(self.tenant, self.student)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("100.00"),
            idempotency_key="credit_for_invalid_tests",
            reference_id="ref_prep",
            description="Prep",
        )

        for invalid_amount in [
            Decimal("0.00"),
            Decimal("-10.00"),
            20,
            20.0,
            "20.00",
            None,
        ]:
            with self.subTest(amount=invalid_amount):
                with self.assertRaises(InvalidAmountError):
                    reserve_funds(
                        tenant=self.tenant,
                        wallet=wallet,
                        amount=invalid_amount,  # type: ignore[arg-type]
                        idempotency_key=f"inv_res_{invalid_amount}",
                        reference_id="ref_inv",
                        description="Invalid amount",
                    )

    def test_reserve_funds_tenant_isolation(self):
        wallet = get_or_create_wallet(self.tenant, self.student)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("100.00"),
            idempotency_key="credit_for_tenant_iso",
            reference_id="ref_iso",
            description="Iso prep",
        )

        with self.assertRaises(TenantMismatchError):
            reserve_funds(
                tenant=self.other_tenant,
                wallet=wallet,
                amount=Decimal("25.00"),
                idempotency_key="cross_tenant_reserve",
                reference_id="ref_cross_res",
                description="Cross tenant reserve",
            )

        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))
