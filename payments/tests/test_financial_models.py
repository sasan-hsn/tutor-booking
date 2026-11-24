from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from accounts.models import Tenant, TermsAcceptance, User
from payments.models import (
    Order,
    Payment,
    PaymentAddress,
    Payout,
    TenantPaymentAccount,
)
from wallet.models import Wallet


class FinancialModelsSeamTestCase(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_sarah",
            password="testpassword",
            role=User.Role.TEACHER,
            first_name="Sarah",
            last_name="Connor",
        )
        self.teacher_profile = self.teacher_user.teacher_profile
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher_profile)

        self.student_user = User.objects.create_user(
            username="student_john",
            password="testpassword",
            role=User.Role.STUDENT,
            first_name="John",
            last_name="Doe",
        )

        self.student_user2 = User.objects.create_user(
            username="student_kyle",
            password="testpassword",
            role=User.Role.STUDENT,
            first_name="Kyle",
            last_name="Reese",
        )

    # ---------------------------------------------------------
    # 1. Model Validation: Order min_topup_amount
    # ---------------------------------------------------------
    def test_order_min_topup_amount_validation(self):
        """Asserts amount >= tenant.min_topup_amount succeeds while lesser raises ValidationError."""
        valid_order = Order(
            tenant=self.tenant,
            student=self.student_user,
            amount=self.tenant.min_topup_amount,
            currency="USD",
            status="PENDING",
            idempotency_key="order_valid_min",
            expires_at=timezone.now() + timedelta(hours=24),
        )
        # full_clean should succeed
        valid_order.full_clean()
        valid_order.save()

        # Lesser amount should raise ValidationError
        invalid_order = Order(
            tenant=self.tenant,
            student=self.student_user,
            amount=self.tenant.min_topup_amount - Decimal("0.01"),
            currency="USD",
            status="PENDING",
            idempotency_key="order_invalid_lesser",
            expires_at=timezone.now() + timedelta(hours=24),
        )
        with self.assertRaises(ValidationError):
            invalid_order.full_clean()

    def test_order_claim_fields(self):
        """Asserts Order student claim fields persist accurately."""
        now = timezone.now()
        order = Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("50.00"),
            currency="USD",
            status="PENDING",
            idempotency_key="order_with_claim",
            expires_at=now + timedelta(hours=24),
            claimed_at=now,
            claimed_txhash="0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            claimed_paid_at=now,
        )
        order.refresh_from_db()
        self.assertEqual(order.claimed_at, now)
        self.assertEqual(
            order.claimed_txhash,
            "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        )
        self.assertEqual(order.claimed_paid_at, now)

    # ---------------------------------------------------------
    # 2. Hardened Foreign Keys & Cascade Deletion Protection
    # ---------------------------------------------------------
    def test_hardened_fk_protection_user_deletion(self):
        """Calling user.delete() on a user with Wallet, Order, or Payment raises ProtectedError."""
        # Test Wallet protection
        Wallet.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            currency="USD",
            settled_balance=Decimal("100.00"),
        )
        with self.assertRaises(ProtectedError):
            self.student_user.delete()

        # Test Order protection
        order = Order.objects.create(
            tenant=self.tenant,
            student=self.student_user2,
            amount=Decimal("25.00"),
            currency="USD",
            status="PENDING",
            idempotency_key="order_fk_protect",
            expires_at=timezone.now() + timedelta(hours=24),
        )
        with self.assertRaises(ProtectedError):
            self.student_user2.delete()

        # Test Payment protection on claimed_by
        user_claimer = User.objects.create_user(
            username="student_claimer", password="testpassword", role=User.Role.STUDENT
        )
        Payment.objects.create(
            tenant=self.tenant,
            order=order,
            claimed_by=user_claimer,
            provider="manual",
            external_id=f"tenant_{self.tenant.id}:manual_fk_test",
            gross_amount=Decimal("25.00"),
            net_amount=Decimal("25.00"),
            currency="USD",
            status="COMPLETED",
        )
        with self.assertRaises(ProtectedError):
            user_claimer.delete()

    def test_hardened_fk_protection_teacher_profile_and_tenant_deletion(self):
        """ProtectedError on teacher_profile.delete() and tenant.delete()."""
        # Attempting to delete teacher_profile when tenant references it
        with self.assertRaises(ProtectedError):
            self.teacher_profile.delete()

        # Creating an entity referencing tenant (e.g. TenantPaymentAccount)
        TenantPaymentAccount.objects.create(
            tenant=self.tenant,
            provider="manual",
            ingestion_enabled=False,
        )
        with self.assertRaises(ProtectedError):
            self.tenant.delete()

    # ---------------------------------------------------------
    # 3. Unique Constraints
    # ---------------------------------------------------------
    def test_payment_address_global_uniqueness(self):
        """Duplicate (provider, address) raises IntegrityError across all tenants."""
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address="0x1111111111111111111111111111111111111111",
            status="ACTIVE",
        )

        # Create another teacher and tenant
        user2 = User.objects.create_user(
            username="teacher2", password="testpassword", role=User.Role.TEACHER
        )
        tenant2 = Tenant.objects.get(teacher_profile=user2.teacher_profile)

        # Attempting to register the same address for tenant2 must raise IntegrityError
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PaymentAddress.objects.create(
                    tenant=tenant2,
                    provider="crypto_usdt",
                    address="0x1111111111111111111111111111111111111111",
                    status="ACTIVE",
                )

    def test_terms_acceptance_uniqueness(self):
        """TermsAcceptance uniqueness on (user, tenant, version) - duplicate fails, new version succeeds."""
        TermsAcceptance.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            version="2026.1",
            ip_address="127.0.0.1",
        )

        # Duplicate version raises IntegrityError
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TermsAcceptance.objects.create(
                    tenant=self.tenant,
                    user=self.student_user,
                    version="2026.1",
                    ip_address="192.168.1.1",
                )

        # New version succeeds
        v2 = TermsAcceptance.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            version="2026.2",
            ip_address="127.0.0.1",
        )
        self.assertEqual(v2.version, "2026.2")

    def test_wallet_uniqueness(self):
        """UniqueConstraint on (tenant, user, currency) for Wallet."""
        Wallet.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            currency="USD",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Wallet.objects.create(
                    tenant=self.tenant,
                    user=self.student_user,
                    currency="USD",
                )

    def test_tenant_payment_account_uniqueness(self):
        """UniqueConstraint on (tenant, provider) for TenantPaymentAccount."""
        TenantPaymentAccount.objects.create(
            tenant=self.tenant,
            provider="manual",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TenantPaymentAccount.objects.create(
                    tenant=self.tenant,
                    provider="manual",
                )

    def test_payout_partial_uniqueness_on_external_ref(self):
        """Multiple NULL external_ref allowed, duplicate non-null raises IntegrityError."""
        # Multiple NULL external_ref
        p1 = Payout.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            provider="crypto_usdt",
            destination="0xabcdef123456",
            external_ref=None,
            amount_gross=Decimal("50.00"),
            amount_net=Decimal("50.00"),
            status="PENDING_CONFIRMATION",
        )
        p2 = Payout.objects.create(
            tenant=self.tenant,
            student=self.student_user2,
            provider="crypto_usdt",
            destination="0x789012345678",
            external_ref=None,
            amount_gross=Decimal("30.00"),
            amount_net=Decimal("30.00"),
            status="PENDING_CONFIRMATION",
        )
        self.assertIsNone(p1.external_ref)
        self.assertIsNone(p2.external_ref)

        # Non-null external_ref
        Payout.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            provider="crypto_usdt",
            destination="0xabcdef123456",
            external_ref="tx_hash_12345",
            amount_gross=Decimal("50.00"),
            amount_net=Decimal("50.00"),
            status="COMPLETED",
        )

        # Duplicate non-null external_ref on same provider raises IntegrityError
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payout.objects.create(
                    tenant=self.tenant,
                    student=self.student_user2,
                    provider="crypto_usdt",
                    destination="0x999999999999",
                    external_ref="tx_hash_12345",
                    amount_gross=Decimal("40.00"),
                    amount_net=Decimal("40.00"),
                    status="COMPLETED",
                )

    # ---------------------------------------------------------
    # 4. Partial Active Address Constraint
    # ---------------------------------------------------------
    def test_partial_active_address_constraint(self):
        """Only 1 ACTIVE address per (tenant, provider), multiple RETIRED allowed."""
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address="0xaddr_active_1",
            status="ACTIVE",
        )

        # Second ACTIVE address for same tenant and provider fails
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PaymentAddress.objects.create(
                    tenant=self.tenant,
                    provider="crypto_usdt",
                    address="0xaddr_active_2",
                    status="ACTIVE",
                )

        # Multiple RETIRED addresses are allowed
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address="0xaddr_retired_1",
            status="RETIRED",
        )
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address="0xaddr_retired_2",
            status="RETIRED",
        )
        self.assertEqual(
            PaymentAddress.objects.filter(tenant=self.tenant, status="RETIRED").count(),
            2,
        )

    # ---------------------------------------------------------
    # 5. Balance Check Constraints
    # ---------------------------------------------------------
    def test_wallet_balance_check_constraints(self):
        """Reserved cannot exceed settled, and neither can be negative."""
        # Valid wallet
        wallet = Wallet.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            currency="USD",
            settled_balance=Decimal("50.00"),
            reserved_balance=Decimal("20.00"),
        )
        self.assertEqual(wallet.available_balance, Decimal("30.00"))

        # Test reserved > settled raises IntegrityError
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Wallet.objects.create(
                    tenant=self.tenant,
                    user=self.student_user2,
                    currency="USD",
                    settled_balance=Decimal("50.00"),
                    reserved_balance=Decimal("60.00"),
                )

        # Test negative settled_balance raises IntegrityError
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Wallet.objects.create(
                    tenant=self.tenant,
                    user=self.student_user2,
                    currency="USD",
                    settled_balance=Decimal("-10.00"),
                    reserved_balance=Decimal("0.00"),
                )

        # Test negative reserved_balance raises IntegrityError
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Wallet.objects.create(
                    tenant=self.tenant,
                    user=self.student_user2,
                    currency="USD",
                    settled_balance=Decimal("10.00"),
                    reserved_balance=Decimal("-5.00"),
                )
