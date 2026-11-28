from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Tenant, User
from accounts.tests.base import RoleTestCase
from booking.models import Booking
from payments.models import Order, Payment, PaymentAddress, Payout
from payments.services import (
    create_topup_order,
    process_manual_topup,
    submit_topup_claim,
)
from payments.tasks import (
    activate_pending_tenant_accounts_task,
    expire_orders_task,
    expire_payout_tokens_task,
)
from wallet.exceptions import WalletFrozenError
from wallet.models import LedgerEntry
from wallet.services import (
    credit_wallet,
    get_or_create_wallet,
    reserve_funds,
    settle_booking,
)
from wallet.tasks import reconcile_wallet_balances_task
from wallet.tests.sentry_helpers import install_sentry_capture


class Runbook1ReconciliationMismatchSimulationTests(TestCase):
    """End-to-end verification of Incident Runbook 1 (Wallet Reconciliation Mismatch)."""

    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_runbook1",
            email="teacher_runbook1@example.com",
            password="testpassword",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher_profile = self.teacher_user.teacher_profile
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher_profile)
        self.student_user = User.objects.create_user(
            username="student_runbook1",
            email="student_runbook1@example.com",
            password="testpassword",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.superuser = User.objects.create_superuser(
            username="admin_runbook1",
            email="admin_runbook1@example.com",
            password="testpassword",
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student_user)

    def test_freeze_audit_repair_unfreeze_restores_parity_without_downtime(self):
        captured = install_sentry_capture(self)
        now = timezone.now()

        # 1. Establish healthy wallet state: $100 settled, $30 reserved for an active booking
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="rb1-credit-100",
            reference_id="ref_rb1_100",
            description="Initial deposit",
        )
        booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher_profile,
            start_at=now - timedelta(hours=2),
            end_at=now - timedelta(hours=1),
            status=Booking.Status.CONFIRMED,
            price=Decimal("30.00"),
            payment_status=Booking.PaymentStatus.RESERVED,
            held_amount=Decimal("30.00"),
        )
        reserve_funds(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("30.00"),
            idempotency_key="rb1-reserve-30",
            reference_id=f"booking_{booking.id}",
            description=f"Hold for booking #{booking.id}",
        )

        # 2. Simulate cache desync (corrupted cached settled_balance)
        self.wallet.settled_balance = Decimal("145.00")
        self.wallet.save(update_fields=["settled_balance"])

        # Trigger reconciliation: detects drift and emits Sentry alert
        self.assertEqual(reconcile_wallet_balances_task(), 1)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["tags"]["wallet_id"], str(self.wallet.id))

        # Step 1: Freeze wallet
        freeze_out = StringIO()
        call_command("freeze_wallet", wallet_id=self.wallet.id, stdout=freeze_out)
        self.wallet.refresh_from_db()
        self.assertTrue(self.wallet.is_frozen)

        # Verify zero downtime for existing holds while blocking new holds
        with self.assertRaises(WalletFrozenError):
            reserve_funds(
                tenant=self.tenant,
                wallet=self.wallet,
                amount=Decimal("20.00"),
                idempotency_key="rb1-blocked-reserve",
                reference_id="booking_blocked",
                description="Should be blocked while frozen",
            )

        settle_booking(
            tenant=self.tenant,
            booking=booking,
            action="consume",
            actor=self.teacher_user,
            reason=Booking.SettlementReason.COMPLETED,
        )
        booking.refresh_from_db()
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.CONSUMED)

        # Step 2: Audit discrepancy
        audit_out = StringIO()
        call_command("audit_wallet", wallet_id=self.wallet.id, stdout=audit_out)
        audit_text = audit_out.getvalue()
        self.assertIn("DISCREPANCY DETECTED", audit_text)
        self.assertIn("115.00", audit_text)  # 145.00 - 30.00 consumed
        self.assertIn("70.00", audit_text)  # expected: 100.00 - 30.00 consumed
        self.assertIn("45.00", audit_text)

        # Step 3: Repair cached snapshot from immutable ledger
        repair_out = StringIO()
        call_command(
            "repair_wallet_cache",
            wallet_id=self.wallet.id,
            commit=True,
            admin=self.superuser.username,
            stdout=repair_out,
        )
        self.assertIn("repaired", repair_out.getvalue().lower())
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("70.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

        # Step 4: Unfreeze wallet
        unfreeze_out = StringIO()
        call_command(
            "freeze_wallet",
            wallet_id=self.wallet.id,
            unfreeze=True,
            stdout=unfreeze_out,
        )
        self.wallet.refresh_from_db()
        self.assertFalse(self.wallet.is_frozen)

        # Step 5: Verify clean reconciliation and normal reservation resumption
        captured.clear()
        self.assertEqual(reconcile_wallet_balances_task(), 0)
        self.assertEqual(captured, [])

        reserve_funds(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("20.00"),
            idempotency_key="rb1-post-unfreeze-reserve",
            reference_id="booking_resumed",
            description="Post-remediation booking hold",
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("70.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("20.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("50.00"))

    def test_freeze_audit_adjust_unfreeze_remediates_physical_liability_mismatch(self):
        # Cache matches ledger ($25.00), but verified offline/on-chain liability is $75.00
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("25.00"),
            idempotency_key="rb1-phys-credit-25",
            reference_id="ref_rb1_phys_25",
            description="Initial deposit",
        )

        call_command("freeze_wallet", wallet_id=self.wallet.id, stdout=StringIO())

        audit_out = StringIO()
        call_command("audit_wallet", wallet_id=self.wallet.id, stdout=audit_out)
        self.assertIn("NO DISCREPANCY DETECTED", audit_out.getvalue())

        adjust_out = StringIO()
        call_command(
            "adjust_wallet",
            wallet_id=self.wallet.id,
            amount="50.00",
            direction="credit",
            reason="INC-303: verified missing external deposit tx 0xabc123",
            admin=self.superuser.username,
            stdout=adjust_out,
        )
        self.assertIn("successfully adjusted", adjust_out.getvalue().lower())

        call_command(
            "freeze_wallet",
            wallet_id=self.wallet.id,
            unfreeze=True,
            stdout=StringIO(),
        )

        self.wallet.refresh_from_db()
        self.assertFalse(self.wallet.is_frozen)
        self.assertEqual(self.wallet.settled_balance, Decimal("75.00"))
        self.assertTrue(
            LedgerEntry.objects.filter(
                wallet=self.wallet,
                entry_type=LedgerEntry.EntryType.ADJUSTMENT_CREDIT,
                amount=Decimal("50.00"),
            ).exists()
        )
        self.assertEqual(reconcile_wallet_balances_task(), 0)


class Runbook2ManualDepositClaimVerificationTests(RoleTestCase):
    """Verification tests for Incident Runbook 2 (Manual Deposit Claim Review)."""

    VALID_ADDRESS = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher_profile = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher_profile)
        PaymentAddress.objects.create(
            tenant=cls.tenant,
            provider="crypto_usdt",
            address=cls.VALID_ADDRESS,
            status=PaymentAddress.Status.ACTIVE,
        )

    def test_verified_claim_with_adjusted_amount_credits_exact_verified_net(self):
        order = create_topup_order(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("50.00"),
        )
        submit_topup_claim(
            tenant=self.tenant,
            student=self.student_user,
            order=order,
            txhash="0xstudent_claimed_typo_hash",
        )

        response = self.teacher_client.get(reverse("payments:teacher_finance"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "0xstudent_claimed_typo_hash")

        # Teacher verifies actual net receipt on BscScan/bank ($48.00) with verified reference
        payment = process_manual_topup(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("48.00"),
            reference_id="bsc_verified_0xdeadbeef",
            admin_user=self.teacher_user,
            notes=f"Verified actual transfer for Order #{order.id}",
        )

        # Idempotent retry with same reference does not double-credit
        retry_payment = process_manual_topup(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("48.00"),
            reference_id="bsc_verified_0xdeadbeef",
            admin_user=self.teacher_user,
        )
        self.assertEqual(payment.id, retry_payment.id)
        self.assertEqual(Payment.objects.filter(tenant=self.tenant).count(), 1)

        wallet = get_or_create_wallet(self.tenant, self.student_user)
        self.assertEqual(wallet.settled_balance, Decimal("48.00"))
        self.assertEqual(reconcile_wallet_balances_task(), 0)

    def test_unverified_claim_remains_uncredited_and_expires_safely(self):
        order = create_topup_order(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("35.00"),
        )
        submit_topup_claim(
            tenant=self.tenant,
            student=self.student_user,
            order=order,
            txhash="0xunverified_claim_hash",
        )

        # Simulate expiry window elapsing while teacher leaves unverified claim uncredited
        Order.objects.filter(pk=order.pk).update(
            expires_at=timezone.now() - timedelta(minutes=5)
        )
        expired_count = expire_orders_task()
        self.assertEqual(expired_count, 1)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.EXPIRED)
        self.assertEqual(Payment.objects.filter(tenant=self.tenant).count(), 0)

        wallet = get_or_create_wallet(self.tenant, self.student_user)
        self.assertEqual(wallet.settled_balance, Decimal("0.00"))
        self.assertEqual(reconcile_wallet_balances_task(), 0)


class Runbook3ServiceDegradedVerificationTests(RoleTestCase):
    """Verification tests for Incident Runbook 3 (Service/Worker Recovery Sweep & Health)."""

    VALID_ADDRESS = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tenant = Tenant.objects.get(
            teacher_profile=cls.teacher_user.teacher_profile
        )

    def test_health_endpoint_returns_ok(self):
        response = self.anon_client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "OK")

    def test_post_recovery_sweep_clears_overdue_items_idempotently(self):
        now = timezone.now()
        pending_addr = PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.VALID_ADDRESS,
            status=PaymentAddress.Status.PENDING_ACTIVATION,
            activates_at=now - timedelta(minutes=10),
        )
        overdue_order = Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key="rb3-overdue-order",
            expires_at=now - timedelta(minutes=10),
        )
        overdue_payout = Payout.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            destination=self.VALID_ADDRESS,
            amount_gross=Decimal("20.00"),
            amount_net=Decimal("20.00"),
            status=Payout.Status.PENDING_CONFIRMATION,
            confirmation_expires_at=now - timedelta(minutes=10),
        )

        self.assertEqual(activate_pending_tenant_accounts_task(), 1)
        self.assertEqual(expire_orders_task(), 1)
        self.assertEqual(expire_payout_tokens_task(), 1)
        self.assertEqual(reconcile_wallet_balances_task(), 0)

        # Second sweep is an idempotent no-op
        self.assertEqual(activate_pending_tenant_accounts_task(), 0)
        self.assertEqual(expire_orders_task(), 0)
        self.assertEqual(expire_payout_tokens_task(), 0)

        pending_addr.refresh_from_db()
        overdue_order.refresh_from_db()
        overdue_payout.refresh_from_db()
        self.assertEqual(pending_addr.status, PaymentAddress.Status.ACTIVE)
        self.assertEqual(overdue_order.status, Order.Status.EXPIRED)
        self.assertEqual(overdue_payout.status, Payout.Status.EXPIRED)
