import csv
import io
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.models import Tenant
from accounts.tests.base import RoleTestCase
from booking.models import Booking
from booking.services import open_dispute
from notifications.models import Notification
from payments.models import Order, Payment, PaymentAddress, Payout
from payments.services import (
    create_payout_request,
    mark_payout_processing,
    process_manual_topup,
    release_payout,
    sanitize_csv_cell,
    settle_payout,
    verify_and_reserve_payout,
)
from wallet.models import LedgerEntry, Wallet
from wallet.services import credit_wallet, get_or_create_wallet, reserve_funds

User = get_user_model()


class TeacherFinancePermissionTests(RoleTestCase):
    """View permission tests for /dashboard/finance/ and manual top-up endpoint."""

    def test_anonymous_user_redirected_to_login_on_finance_dashboard(self):
        response = self.anon_client.get("/dashboard/finance/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_student_user_forbidden_on_finance_dashboard(self):
        response = self.student_client.get("/dashboard/finance/")
        self.assertEqual(response.status_code, 403)

    def test_teacher_user_can_access_finance_dashboard(self):
        response = self.teacher_client.get("/dashboard/finance/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            reverse("payments:teacher_finance"),
            "/dashboard/finance/",
        )
        self.assertContains(response, 'id="manualTopupModal"')
        self.assertContains(response, 'id="manualTopupForm"')
        self.assertContains(response, 'id="manualTopupIdempotencyKey"')
        self.assertContains(response, "teacher_finance.js")


class TeacherFinanceOverviewAndClaimsTests(RoleTestCase):
    """Tests for tenant financial overview, recent ledger entries, and claims queue."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

        cls.other_teacher_user = User.objects.create_user(
            username="other_finance_teacher",
            email="other_finance_teacher@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        cls.other_tenant = Tenant.objects.get(
            teacher_profile=cls.other_teacher_user.teacher_profile
        )

    def test_overview_displays_student_balances_holds_and_recent_ledger(self):
        wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("80.00"),
            idempotency_key="overview-credit-80",
            reference_id="ref_seed_80",
            description="Initial manual credit",
        )
        reserve_funds(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("25.00"),
            idempotency_key="overview-reserve-25",
            reference_id="booking_101",
            description="Lesson hold",
        )
        now = timezone.now()
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, minutes=50),
            status=Booking.Status.CONFIRMED,
            price=Decimal("25.00"),
            payment_status=Booking.PaymentStatus.RESERVED,
            held_amount=Decimal("25.00"),
        )

        # Unrelated tenant wallet/ledger must be isolated
        other_wallet = get_or_create_wallet(self.other_tenant, self.student_user)
        credit_wallet(
            tenant=self.other_tenant,
            wallet=other_wallet,
            amount=Decimal("500.00"),
            idempotency_key="other-tenant-500",
            reference_id="other_ref_500",
            description="Other tenant credit",
        )

        response = self.teacher_client.get(reverse("payments:teacher_finance"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "$80.00")
        self.assertContains(response, "$55.00")
        self.assertContains(response, "$25.00")
        self.assertContains(response, "Initial manual credit")
        self.assertContains(response, "ref_seed_80")
        self.assertNotContains(response, "Other tenant credit")
        self.assertNotContains(response, "500.00")

    def test_claims_queue_displays_only_pending_claimed_orders_for_tenant(self):
        now = timezone.now()
        self.student_user.first_name = "Alice"
        self.student_user.last_name = "Claimer"
        self.student_user.save(update_fields=["first_name", "last_name"])

        claimed_order = Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("45.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key="claim-pending-1",
            expires_at=now + timedelta(hours=24),
            claimed_at=now - timedelta(minutes=10),
            claimed_paid_at=now - timedelta(minutes=10),
            claimed_txhash="0xclaimedhash123",
        )
        # Unclaimed order should not appear
        Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("33.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key="unclaimed-pending-2",
            expires_at=now + timedelta(hours=24),
        )
        # Completed claimed order should not appear
        Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("60.00"),
            currency="USD",
            status=Order.Status.COMPLETED,
            idempotency_key="claim-completed-3",
            expires_at=now + timedelta(hours=24),
            claimed_at=now - timedelta(hours=2),
            claimed_paid_at=now - timedelta(hours=2),
            claimed_txhash="0xcompletedhash999",
            completed_at=now - timedelta(hours=1),
        )
        # Expired claimed order should not appear
        Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("77.00"),
            currency="USD",
            status=Order.Status.EXPIRED,
            idempotency_key="claim-expired-4",
            expires_at=now - timedelta(minutes=1),
            claimed_at=now - timedelta(hours=2),
            claimed_paid_at=now - timedelta(hours=2),
            claimed_txhash="0xexpiredhash888",
        )
        # Other tenant's claim should not appear
        Order.objects.create(
            tenant=self.other_tenant,
            student=self.student_user,
            amount=Decimal("99.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key="other-tenant-claim-4",
            expires_at=now + timedelta(hours=24),
            claimed_at=now - timedelta(minutes=5),
            claimed_paid_at=now - timedelta(minutes=5),
            claimed_txhash="0xothertenanthash",
        )

        response = self.teacher_client.get(reverse("payments:teacher_finance"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alice Claimer")
        self.assertContains(response, "$45.00")
        self.assertContains(response, "0xclaimedhash123")
        self.assertContains(response, "Credit Deposit")
        self.assertContains(response, f'data-order-id="{claimed_order.id}"')
        self.assertContains(response, f'data-student-id="{self.student_user.id}"')
        self.assertContains(response, 'data-amount="45.00"')
        self.assertContains(response, 'data-reference="0xclaimedhash123"')

        self.assertNotContains(response, "33.00")
        self.assertNotContains(response, "0xcompletedhash999")
        self.assertNotContains(response, "0xexpiredhash888")
        self.assertNotContains(response, "0xothertenanthash")


class ManualTopUpViewTests(RoleTestCase):
    """Tests for /dashboard/finance/manual-topup/ submission, idempotency, and validation."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

    def test_manual_topup_requires_teacher_and_post(self):
        url = "/dashboard/finance/manual-topup/"
        anon_resp = self.anon_client.post(url, {})
        self.assertEqual(anon_resp.status_code, 302)
        self.assertIn("/accounts/login/", anon_resp.url)

        student_resp = self.student_client.post(url, {})
        self.assertEqual(student_resp.status_code, 403)

        get_resp = self.teacher_client.get(url)
        self.assertEqual(get_resp.status_code, 405)

    def test_manual_topup_valid_post_credits_wallet_and_creates_ledger_entry(self):
        url = reverse("payments:manual_topup")
        response = self.teacher_client.post(
            url,
            {
                "student": self.student_user.id,
                "amount": "40.00",
                "reference_id": "BANK_REF_1001",
                "notes": "Verified wire transfer",
                "idempotency_key": "11111111-1111-4111-8111-111111111111",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("overview_html", data)
        self.assertIn("ledger_html", data)
        self.assertIn("claims_html", data)

        payment = Payment.objects.get(
            external_id=f"tenant_{self.tenant.id}:manual_BANK_REF_1001"
        )
        self.assertEqual(payment.status, Payment.Status.COMPLETED)
        self.assertEqual(payment.net_amount, Decimal("40.00"))
        self.assertEqual(payment.claimed_by, self.student_user)

        wallet = Wallet.objects.get(
            tenant=self.tenant, user=self.student_user, currency="USD"
        )
        self.assertEqual(wallet.settled_balance, Decimal("40.00"))
        self.assertEqual(wallet.available_balance, Decimal("40.00"))

        entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:payment:{payment.id}:credit"
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.CREDIT)
        self.assertEqual(entry.amount, Decimal("40.00"))
        self.assertIn("BANK_REF_1001", data["ledger_html"])
        self.assertNotIn(f"tenant_{self.tenant.id}:manual_", data["ledger_html"])
        self.assertIn('title="BANK_REF_1001"', data["ledger_html"])
        self.assertIn('data-copy-text="BANK_REF_1001"', data["ledger_html"])
        self.assertIn("bi-clipboard", data["ledger_html"])
        self.assertIn("Verified wire transfer", data["ledger_html"])
        self.assertIn('title="Verified wire transfer"', data["ledger_html"])

    def test_ledger_table_uses_clean_manual_topup_fallback_without_notes(self):
        long_tx = "0x" + "a" * 64
        response = self.teacher_client.post(
            reverse("payments:manual_topup"),
            {
                "student": self.student_user.id,
                "amount": "35.00",
                "reference_id": long_tx,
                "notes": "",
                "idempotency_key": "11111111-1111-4111-8111-222222222222",
            },
        )
        self.assertEqual(response.status_code, 200)
        ledger_html = response.json()["ledger_html"]
        self.assertIn(long_tx, ledger_html)
        self.assertNotIn(f"tenant_{self.tenant.id}:manual_", ledger_html)
        self.assertIn(f'title="{long_tx}"', ledger_html)
        self.assertIn(f'data-copy-text="{long_tx}"', ledger_html)
        self.assertIn("Manual top-up", ledger_html)
        self.assertIn('title="Manual top-up"', ledger_html)
        self.assertIn("text-truncate", ledger_html)

    def test_manual_topup_duplicate_reference_returns_validation_error(self):
        url = reverse("payments:manual_topup")
        first = self.teacher_client.post(
            url,
            {
                "student": self.student_user.id,
                "amount": "25.00",
                "reference_id": "DUP_REF_001",
                "notes": "First submission",
                "idempotency_key": "22222222-2222-4222-8222-222222222221",
            },
        )
        self.assertEqual(first.status_code, 200)

        duplicate = self.teacher_client.post(
            url,
            {
                "student": self.student_user.id,
                "amount": "25.00",
                "reference_id": "DUP_REF_001",
                "notes": "Second submission",
                "idempotency_key": "22222222-2222-4222-8222-222222222222",
            },
        )
        self.assertEqual(duplicate.status_code, 400)
        data = duplicate.json()
        self.assertFalse(data["success"])
        expected_msg = (
            "A manual deposit with this receipt reference has already been credited."
        )
        self.assertEqual(data["error"], expected_msg)
        self.assertIn(expected_msg, data["errors"]["reference_id"])

        self.assertEqual(
            Payment.objects.filter(
                external_id=f"tenant_{self.tenant.id}:manual_DUP_REF_001"
            ).count(),
            1,
        )
        wallet = Wallet.objects.get(tenant=self.tenant, user=self.student_user)
        self.assertEqual(wallet.settled_balance, Decimal("25.00"))

    def test_manual_topup_same_idempotency_key_retry_is_idempotent(self):
        url = reverse("payments:manual_topup")
        payload = {
            "student": self.student_user.id,
            "amount": "30.00",
            "reference_id": "IDEM_REF_001",
            "notes": "Retry test",
            "idempotency_key": "33333333-3333-4333-8333-333333333333",
        }
        resp1 = self.teacher_client.post(url, payload)
        resp2 = self.teacher_client.post(url, payload)
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)

        wallet = Wallet.objects.get(tenant=self.tenant, user=self.student_user)
        self.assertEqual(wallet.settled_balance, Decimal("30.00"))
        self.assertEqual(Payment.objects.filter(tenant=self.tenant).count(), 1)

    def test_manual_topup_reused_idempotency_key_with_different_ref_rejected(self):
        url = reverse("payments:manual_topup")
        shared_key = "33333333-3333-4333-8333-999999999999"
        first = self.teacher_client.post(
            url,
            {
                "student": self.student_user.id,
                "amount": "25.00",
                "reference_id": "IDEM_FIRST_REF",
                "idempotency_key": shared_key,
            },
        )
        self.assertEqual(first.status_code, 200)

        conflicting = self.teacher_client.post(
            url,
            {
                "student": self.student_user.id,
                "amount": "25.00",
                "reference_id": "IDEM_DIFFERENT_REF",
                "idempotency_key": shared_key,
            },
        )
        self.assertEqual(conflicting.status_code, 400)
        self.assertFalse(conflicting.json()["success"])
        self.assertEqual(Payment.objects.filter(tenant=self.tenant).count(), 1)

    def test_manual_topup_below_min_amount_rejected(self):
        url = reverse("payments:manual_topup")
        response = self.teacher_client.post(
            url,
            {
                "student": self.student_user.id,
                "amount": "10.00",
                "reference_id": "LOW_AMT_001",
                "idempotency_key": "44444444-4444-4444-8444-444444444444",
            },
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("amount", data["errors"])
        self.assertFalse(Payment.objects.filter(tenant=self.tenant).exists())

    def test_manual_topup_for_claimed_order_completes_order(self):
        now = timezone.now()
        claimed_order = Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("50.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key="order-claim-complete-1",
            expires_at=now + timedelta(hours=24),
            claimed_at=now - timedelta(minutes=5),
            claimed_paid_at=now - timedelta(minutes=5),
            claimed_txhash="0xclaimtx500",
        )

        response = self.teacher_client.post(
            reverse("payments:manual_topup"),
            {
                "student": self.student_user.id,
                "amount": "50.00",
                "reference_id": "0xclaimtx500",
                "notes": f"Top-up claim for Order #{claimed_order.id}",
                "idempotency_key": "55555555-5555-4555-8555-555555555555",
            },
        )
        self.assertEqual(response.status_code, 200)
        claimed_order.refresh_from_db()
        self.assertEqual(claimed_order.status, Order.Status.COMPLETED)
        self.assertIsNotNone(claimed_order.completed_at)
        self.assertNotIn("0xclaimtx500", response.json()["claims_html"])


class TeacherPayoutReviewQueueTests(RoleTestCase):
    """Tests for Teacher Dashboard Payout (Refund) Review Queue & payout actions."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

        cls.other_teacher_user = User.objects.create_user(
            username="other_payout_teacher",
            email="other_payout_teacher@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        cls.other_tenant = Tenant.objects.get(
            teacher_profile=cls.other_teacher_user.teacher_profile
        )

    def setUp(self):
        super().setUp()
        self.student_user.first_name = "Alice"
        self.student_user.last_name = "Refund"
        self.student_user.email = "alice.refund@example.com"
        self.student_user.save(update_fields=["first_name", "last_name", "email"])

        self.wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("200.00"),
            idempotency_key="payout-view-seed-200",
            reference_id="seed_200",
            description="Seed wallet for payout review queue tests",
        )

    def _create_verified_payout(
        self,
        amount: Decimal = Decimal("40.00"),
        destination: str = "0x1111111111111111111111111111111111111111",
        tenant: Tenant | None = None,
    ) -> Payout:
        target_tenant = tenant or self.tenant
        if target_tenant != self.tenant:
            other_wallet = get_or_create_wallet(target_tenant, self.student_user)
            credit_wallet(
                tenant=target_tenant,
                wallet=other_wallet,
                amount=Decimal("200.00"),
                idempotency_key=f"other-seed-{target_tenant.id}-{amount}",
                reference_id=f"other_seed_{target_tenant.id}",
                description="Other tenant seed",
            )
        payout = create_payout_request(
            tenant=target_tenant,
            student=self.student_user,
            amount_gross=amount,
            destination=destination,
        )
        verify_and_reserve_payout(
            tenant=target_tenant,
            payout=payout,
            raw_token=payout.raw_token,
        )
        payout.refresh_from_db()
        return payout

    def test_payout_action_permissions_and_cross_tenant_isolation(self):
        payout = self._create_verified_payout()
        other_payout = self._create_verified_payout(
            amount=Decimal("30.00"),
            destination="0x2222222222222222222222222222222222222222",
            tenant=self.other_tenant,
        )

        endpoints = [
            reverse("payments:payout_mark_processing", args=[payout.id]),
            reverse("payments:payout_complete", args=[payout.id]),
            reverse("payments:payout_reject", args=[payout.id]),
        ]

        for url in endpoints:
            anon_resp = self.anon_client.post(url, {})
            self.assertEqual(anon_resp.status_code, 302)
            self.assertIn("/accounts/login/", anon_resp.url)

            student_resp = self.student_client.post(url, {})
            self.assertEqual(student_resp.status_code, 403)

            get_resp = self.teacher_client.get(url)
            self.assertEqual(get_resp.status_code, 405)

        # Cross-tenant payout access must return 404
        for url_name in (
            "payments:payout_mark_processing",
            "payments:payout_complete",
            "payments:payout_reject",
        ):
            resp = self.teacher_client.post(
                reverse(url_name, args=[other_payout.id]), {}
            )
            self.assertEqual(resp.status_code, 404)

    def test_queue_listing_separates_active_and_historical_payouts(self):
        requested_payout = self._create_verified_payout(
            amount=Decimal("25.00"),
            destination="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        )
        processing_payout = self._create_verified_payout(
            amount=Decimal("35.00"),
            destination="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        )
        mark_payout_processing(
            tenant=self.tenant,
            payout=processing_payout,
            admin_user=self.teacher_user,
        )

        completed_payout = self._create_verified_payout(
            amount=Decimal("15.00"),
            destination="0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
        )
        mark_payout_processing(
            tenant=self.tenant,
            payout=completed_payout,
            admin_user=self.teacher_user,
        )
        settle_payout(
            tenant=self.tenant,
            payout=completed_payout,
            external_ref="0xcompletedref999",
            admin_user=self.teacher_user,
        )

        rejected_payout = self._create_verified_payout(
            amount=Decimal("18.00"),
            destination="0xDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD",
        )
        release_payout(
            tenant=self.tenant,
            payout=rejected_payout,
            reason="Wrong network address",
            admin_user=self.teacher_user,
        )

        # Unconfirmed and expired payouts must not appear in the review queue
        create_payout_request(
            tenant=self.tenant,
            student=self.student_user,
            amount_gross=Decimal("12.00"),
            destination="0xEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE",
        )
        other_payout = self._create_verified_payout(
            amount=Decimal("99.00"),
            destination="0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
            tenant=self.other_tenant,
        )

        response = self.teacher_client.get(reverse("payments:teacher_finance"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")

        # Active queue shows REQUESTED and PROCESSING with full details
        self.assertContains(response, "Alice Refund")
        self.assertContains(response, "alice.refund@example.com")
        self.assertContains(response, "$25.00")
        self.assertContains(response, "$35.00")
        self.assertContains(response, "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        self.assertContains(response, "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB")
        self.assertContains(response, f'data-payout-id="{requested_payout.id}"')
        self.assertContains(response, f'data-payout-id="{processing_payout.id}"')
        self.assertContains(response, "Mark Processing")
        self.assertContains(response, "Complete Payout")
        self.assertContains(response, "Reject Payout")

        # Historical table displays COMPLETED and REJECTED, excluded from active queue
        self.assertIn("0xcompletedref999", html)
        self.assertIn("$15.00", html)
        self.assertIn("$18.00", html)
        self.assertIn(f'data-history-payout-id="{completed_payout.id}"', html)
        self.assertIn(f'data-history-payout-id="{rejected_payout.id}"', html)
        self.assertNotIn(f'data-payout-id="{completed_payout.id}"', html)
        self.assertNotIn(f'data-payout-id="{rejected_payout.id}"', html)

        # Unconfirmed and other-tenant payouts must be absent
        self.assertNotIn("0xEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE", html)
        self.assertNotIn("0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF", html)
        self.assertNotIn(f'data-payout-id="{other_payout.id}"', html)

    def test_mark_processing_transitions_status_without_altering_balances(self):
        payout = self._create_verified_payout(amount=Decimal("40.00"))
        mail.outbox.clear()

        url = reverse("payments:payout_mark_processing", args=[payout.id])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["payout_status"], Payout.Status.PROCESSING)
        self.assertIn("payouts_html", data)
        self.assertIn("overview_html", data)
        self.assertIn("ledger_html", data)
        self.assertIn("Complete Payout", data["payouts_html"])

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.PROCESSING)
        self.assertIsNotNone(payout.processed_at)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("200.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("40.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("160.00"))

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["alice.refund@example.com"])

        # Re-posting mark processing on an already PROCESSING payout returns 409 Conflict
        conflict_resp = self.teacher_client.post(url)
        self.assertEqual(conflict_resp.status_code, 409)
        self.assertFalse(conflict_resp.json()["success"])

    def test_complete_payout_with_valid_txhash_settles_ledger_and_clears_queue(self):
        payout = self._create_verified_payout(amount=Decimal("40.00"))
        mark_payout_processing(
            tenant=self.tenant,
            payout=payout,
            admin_user=self.teacher_user,
        )
        mail.outbox.clear()

        txhash = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        url = reverse("payments:payout_complete", args=[payout.id])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(url, {"txhash": txhash})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["payout_status"], Payout.Status.COMPLETED)
        self.assertNotIn(f'data-payout-id="{payout.id}"', data["payouts_html"])
        self.assertIn(f'data-history-payout-id="{payout.id}"', data["payouts_html"])
        self.assertIn(txhash, data["payouts_html"])
        self.assertIn(txhash, data["ledger_html"])

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.COMPLETED)
        self.assertEqual(payout.external_ref, txhash)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("160.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("160.00"))

        settle_entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:payout:{payout.id}:settle"
        )
        self.assertEqual(settle_entry.entry_type, LedgerEntry.EntryType.PAYOUT_SETTLE)
        self.assertEqual(settle_entry.amount, Decimal("40.00"))

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["alice.refund@example.com"])
        self.assertIn(txhash, mail.outbox[0].body)

    def test_complete_payout_validation_and_duplicate_txhash_errors(self):
        first_payout = self._create_verified_payout(amount=Decimal("20.00"))
        mark_payout_processing(
            tenant=self.tenant,
            payout=first_payout,
            admin_user=self.teacher_user,
        )
        settle_payout(
            tenant=self.tenant,
            payout=first_payout,
            external_ref="0xalreadyusedhash",
            admin_user=self.teacher_user,
        )

        # REQUESTED payout cannot be completed directly (must be PROCESSING -> 409)
        requested_payout = self._create_verified_payout(amount=Decimal("25.00"))
        conflict_resp = self.teacher_client.post(
            reverse("payments:payout_complete", args=[requested_payout.id]),
            {"txhash": "0xnewhash123"},
        )
        self.assertEqual(conflict_resp.status_code, 409)
        self.assertFalse(conflict_resp.json()["success"])

        mark_payout_processing(
            tenant=self.tenant,
            payout=requested_payout,
            admin_user=self.teacher_user,
        )
        complete_url = reverse("payments:payout_complete", args=[requested_payout.id])

        # Empty txhash -> 400 validation error
        empty_resp = self.teacher_client.post(complete_url, {"txhash": "   "})
        self.assertEqual(empty_resp.status_code, 400)
        self.assertFalse(empty_resp.json()["success"])
        self.assertIn("txhash", empty_resp.json()["errors"])

        # Duplicate txhash -> 400 validation error (no 500 IntegrityError)
        dup_resp = self.teacher_client.post(
            complete_url, {"txhash": "0xalreadyusedhash"}
        )
        self.assertEqual(dup_resp.status_code, 400)
        self.assertFalse(dup_resp.json()["success"])
        self.assertIn("txhash", dup_resp.json()["errors"])

    def test_reject_payout_requires_reason_and_restores_balance_on_valid_post(self):
        payout = self._create_verified_payout(amount=Decimal("45.00"))
        reject_url = reverse("payments:payout_reject", args=[payout.id])

        # Missing reason returns 400 form error
        invalid_resp = self.teacher_client.post(reject_url, {"reason": "   "})
        self.assertEqual(invalid_resp.status_code, 400)
        invalid_data = invalid_resp.json()
        self.assertFalse(invalid_data["success"])
        self.assertIn("reason", invalid_data["errors"])

        # Reason exceeding 200-char form limit returns 400 rather than DB DataError 500
        too_long_resp = self.teacher_client.post(reject_url, {"reason": "X" * 201})
        self.assertEqual(too_long_resp.status_code, 400)
        self.assertIn("reason", too_long_resp.json()["errors"])

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.REQUESTED)

        # Valid rejection restores available balance, marks REJECTED, and emails student
        mail.outbox.clear()
        reason_text = "Invalid receiving address format."
        with self.captureOnCommitCallbacks(execute=True):
            valid_resp = self.teacher_client.post(reject_url, {"reason": reason_text})

        self.assertEqual(valid_resp.status_code, 200)
        data = valid_resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["payout_status"], Payout.Status.REJECTED)
        self.assertNotIn(f'data-payout-id="{payout.id}"', data["payouts_html"])
        self.assertIn(f'data-history-payout-id="{payout.id}"', data["payouts_html"])

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.REJECTED)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("200.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("200.00"))

        release_entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:payout:{payout.id}:release"
        )
        self.assertEqual(release_entry.entry_type, LedgerEntry.EntryType.PAYOUT_RELEASE)
        self.assertIn(reason_text, release_entry.description)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["alice.refund@example.com"])
        self.assertIn(reason_text, mail.outbox[0].body)

        # Re-rejecting already REJECTED payout returns 409 Conflict
        conflict_resp = self.teacher_client.post(
            reject_url, {"reason": "Second reject"}
        )
        self.assertEqual(conflict_resp.status_code, 409)
        self.assertFalse(conflict_resp.json()["success"])


class TeacherDisputeReviewQueueTests(RoleTestCase):
    """Tests for Teacher Dashboard Finance Dispute Queue & dispute resolution."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)
        cls.tenant.payments_enabled = True
        cls.tenant.save(update_fields=["payments_enabled"])

        cls.other_teacher_user = User.objects.create_user(
            username="other_dispute_teacher",
            email="other_dispute_teacher@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        cls.other_tenant = Tenant.objects.get(
            teacher_profile=cls.other_teacher_user.teacher_profile
        )
        cls.other_tenant.payments_enabled = True
        cls.other_tenant.save(update_fields=["payments_enabled"])

    def setUp(self):
        super().setUp()
        self.teacher_user.email = "mary.teacher@example.com"
        self.teacher_user.save(update_fields=["email"])
        self.student_user.first_name = "Alice"
        self.student_user.last_name = "Disputer"
        self.student_user.email = "alice.disputer@example.com"
        self.student_user.save(update_fields=["first_name", "last_name", "email"])

        self.wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="dispute-queue-seed-100",
            reference_id="dispute_seed_100",
            description="Seed wallet for dispute queue tests",
        )

    def _create_disputed_booking(
        self,
        *,
        teacher=None,
        tenant=None,
        price=Decimal("25.00"),
        reason="Student reported audio outage.",
        opened_hours_ago=6,
    ) -> Booking:
        target_teacher = teacher or self.teacher
        target_tenant = tenant or self.tenant
        target_wallet = get_or_create_wallet(target_tenant, self.student_user)
        if target_wallet.available_balance < price:
            credit_wallet(
                tenant=target_tenant,
                wallet=target_wallet,
                amount=price,
                idempotency_key=f"dispute-extra-{target_tenant.id}-{timezone.now().timestamp()}",
                reference_id="extra_seed",
                description="Extra seed",
            )

        now = timezone.now()
        end_at = now - timedelta(hours=opened_hours_ago + 1)
        start_at = end_at - timedelta(minutes=50)
        booking = Booking.objects.create(
            student=self.student_user,
            teacher=target_teacher,
            start_at=start_at,
            end_at=end_at,
            duration_minutes=50,
            lesson_type=Booking.LessonType.REGULAR,
            status=Booking.Status.CONFIRMED,
            price=price,
            payment_status=Booking.PaymentStatus.RESERVED,
            held_amount=price,
        )
        reserve_funds(
            tenant=target_tenant,
            wallet=target_wallet,
            amount=price,
            idempotency_key=f"tenant:{target_tenant.id}:booking:{booking.id}:reserve",
            reference_id=f"booking_{booking.id}",
            description=f"Booking hold: booking #{booking.id}",
        )
        open_dispute(
            booking=booking,
            student=self.student_user,
            reason=reason,
            now=now - timedelta(hours=opened_hours_ago),
        )
        booking.refresh_from_db()
        return booking

    def test_dispute_resolve_permissions_and_cross_tenant_isolation(self):
        disputed = self._create_disputed_booking()
        other_disputed = self._create_disputed_booking(
            teacher=self.other_teacher_user.teacher_profile,
            tenant=self.other_tenant,
            reason="Other teacher dispute",
        )

        url = reverse("payments:dispute_resolve", args=[disputed.id])
        anon_resp = self.anon_client.post(url, {"resolution": "STUDENT_FAVOR"})
        self.assertEqual(anon_resp.status_code, 302)
        self.assertIn("/accounts/login/", anon_resp.url)

        student_resp = self.student_client.post(url, {"resolution": "STUDENT_FAVOR"})
        self.assertEqual(student_resp.status_code, 403)

        get_resp = self.teacher_client.get(url)
        self.assertEqual(get_resp.status_code, 405)

        cross_resp = self.teacher_client.post(
            reverse("payments:dispute_resolve", args=[other_disputed.id]),
            {"resolution": "STUDENT_FAVOR"},
        )
        self.assertEqual(cross_resp.status_code, 404)

    def test_finance_tab_lists_active_disputes_with_details_countdown_and_reason(self):
        active_dispute = self._create_disputed_booking(
            price=Decimal("32.50"),
            reason="Meeting link failed to connect after 10 minutes.",
            opened_hours_ago=24,
        )
        # Non-disputing booking must not appear in dispute queue
        now = timezone.now()
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now - timedelta(days=5),
            end_at=now - timedelta(days=5) + timedelta(minutes=50),
            status=Booking.Status.COMPLETED,
            price=Decimal("25.00"),
            payment_status=Booking.PaymentStatus.CONSUMED,
            held_amount=Decimal("25.00"),
            dispute_reason="Should not appear",
        )
        # Other teacher's dispute must not appear
        other_dispute = self._create_disputed_booking(
            teacher=self.other_teacher_user.teacher_profile,
            tenant=self.other_tenant,
            reason="Hidden other teacher dispute reason",
        )

        response = self.teacher_client.get(reverse("payments:teacher_finance"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")

        self.assertIn("Dispute Review Queue", html)
        self.assertIn(f'data-dispute-booking-id="{active_dispute.id}"', html)
        self.assertIn("Alice Disputer", html)
        self.assertIn("$32.50", html)
        self.assertIn("Meeting link failed to connect after 10 minutes.", html)
        self.assertIn('class="finance-dispute-reason-row"', html)
        self.assertIn('colspan="5"', html)
        expected_deadline = (
            active_dispute.dispute_opened_at
            + timedelta(days=settings.DISPUTE_WINDOW_DAYS)
        ).isoformat()
        self.assertIn(f'data-dispute-deadline="{expected_deadline}"', html)
        self.assertIn("Resolve in Student Favor", html)
        self.assertIn("Resolve in Teacher Favor", html)

        self.assertNotIn("Should not appear", html)
        self.assertNotIn("Hidden other teacher dispute reason", html)
        self.assertNotIn(f'data-dispute-booking-id="{other_dispute.id}"', html)

    def test_resolve_in_student_favor_releases_hold_updates_booking_and_notifies(self):
        disputed = self._create_disputed_booking(price=Decimal("25.00"))
        mail.outbox.clear()

        url = reverse("payments:dispute_resolve", args=[disputed.id])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(url, {"resolution": "STUDENT_FAVOR"})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["booking_status"], Booking.Status.RESOLVED)
        self.assertEqual(data["dispute_resolution"], "STUDENT_FAVOR")
        self.assertNotIn(
            f'data-dispute-booking-id="{disputed.id}"', data["disputes_html"]
        )
        self.assertIn("overview_html", data)
        self.assertIn("ledger_html", data)

        disputed.refresh_from_db()
        self.assertEqual(disputed.status, Booking.Status.RESOLVED)
        self.assertEqual(disputed.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            disputed.settlement_reason, Booking.SettlementReason.DISPUTE_STUDENT_FAVOR
        )
        self.assertEqual(disputed.dispute_resolution, "STUDENT_FAVOR")
        self.assertEqual(disputed.dispute_resolved_by, self.teacher_user)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("100.00"))

        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.DISPUTE_RESOLVED,
                metadata__booking_id=disputed.id,
            ).count(),
            2,
        )

        # Second resolution attempt must return 409 Conflict
        conflict_resp = self.teacher_client.post(url, {"resolution": "TEACHER_FAVOR"})
        self.assertEqual(conflict_resp.status_code, 409)
        self.assertFalse(conflict_resp.json()["success"])

    def test_resolve_in_teacher_favor_consumes_hold_updates_booking_and_notifies(self):
        disputed = self._create_disputed_booking(price=Decimal("25.00"))
        mail.outbox.clear()

        url = reverse("payments:dispute_resolve", args=[disputed.id])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(url, {"resolution": "TEACHER_FAVOR"})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["booking_status"], Booking.Status.COMPLETED)
        self.assertEqual(data["dispute_resolution"], "TEACHER_FAVOR")
        self.assertNotIn(
            f'data-dispute-booking-id="{disputed.id}"', data["disputes_html"]
        )

        disputed.refresh_from_db()
        self.assertEqual(disputed.status, Booking.Status.COMPLETED)
        self.assertEqual(disputed.payment_status, Booking.PaymentStatus.CONSUMED)
        self.assertEqual(
            disputed.settlement_reason, Booking.SettlementReason.DISPUTE_TEACHER_FAVOR
        )
        self.assertEqual(disputed.dispute_resolution, "TEACHER_FAVOR")
        self.assertEqual(disputed.dispute_resolved_by, self.teacher_user)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("75.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("75.00"))

        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.DISPUTE_RESOLVED,
                metadata__booking_id=disputed.id,
            ).count(),
            2,
        )

    def test_resolve_dispute_invalid_resolution_returns_400(self):
        disputed = self._create_disputed_booking()
        resp = self.teacher_client.post(
            reverse("payments:dispute_resolve", args=[disputed.id]),
            {"resolution": "INVALID"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["success"])


class TeacherLedgerCsvExportTests(RoleTestCase):
    """Tests for Finance tab formula-sanitized CSV ledger export."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

        cls.other_teacher_user = User.objects.create_user(
            username="other_csv_teacher",
            email="other_csv_teacher@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        cls.other_tenant = Tenant.objects.get(
            teacher_profile=cls.other_teacher_user.teacher_profile
        )

    def test_sanitize_csv_cell_prefixes_all_formula_injection_triggers(self):
        self.assertEqual(sanitize_csv_cell("=SUM(A1:A10)"), "'=SUM(A1:A10)")
        self.assertEqual(sanitize_csv_cell("+cmd|' /C calc'"), "'+cmd|' /C calc'")
        self.assertEqual(sanitize_csv_cell("-10"), "'-10")
        self.assertEqual(sanitize_csv_cell("@user"), "'@user")
        self.assertEqual(sanitize_csv_cell("\t=1+1"), "'\t=1+1")
        self.assertEqual(sanitize_csv_cell("\r=2+2"), "'\r=2+2")
        self.assertEqual(sanitize_csv_cell("Normal text"), "Normal text")
        self.assertEqual(sanitize_csv_cell(None), "")

    def test_csv_export_permissions_and_button_presence(self):
        export_url = reverse("payments:export_ledger_csv")

        anon_resp = self.anon_client.get(export_url)
        self.assertEqual(anon_resp.status_code, 302)
        self.assertIn("/accounts/login/", anon_resp.url)

        student_resp = self.student_client.get(export_url)
        self.assertEqual(student_resp.status_code, 403)

        finance_resp = self.teacher_client.get(reverse("payments:teacher_finance"))
        self.assertContains(finance_resp, "Export Ledger CSV")
        self.assertContains(finance_resp, f'href="{export_url}"')

    def test_csv_export_includes_tenant_ledger_entries_and_sanitizes_formula_injection(
        self,
    ):
        self.student_user.first_name = "Alice"
        self.student_user.last_name = "Ledger"
        self.student_user.email = "alice.ledger@example.com"
        self.student_user.save(update_fields=["first_name", "last_name", "email"])

        process_manual_topup(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("50.00"),
            reference_id="=SUM(A1:A10)",
            admin_user=self.teacher_user,
            notes="+cmd|' /C calc'",
        )
        wallet = get_or_create_wallet(self.tenant, self.student_user)
        reserve_funds(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("20.00"),
            idempotency_key="csv-reserve-20",
            reference_id="-10",
            description="@user",
        )
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("5.00"),
            idempotency_key="csv-tab-5",
            reference_id="tab_ref",
            description='\t=HYPERLINK("http://evil.example")',
        )

        # Other tenant entry must be excluded
        other_wallet = get_or_create_wallet(self.other_tenant, self.student_user)
        credit_wallet(
            tenant=self.other_tenant,
            wallet=other_wallet,
            amount=Decimal("999.00"),
            idempotency_key="csv-other-tenant-999",
            reference_id="other_secret_ref",
            description="Secret other tenant transaction",
        )

        resp = self.teacher_client.get(reverse("payments:export_ledger_csv"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        self.assertIn("attachment; filename=", resp["Content-Disposition"])

        content = resp.content.decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(content)))
        self.assertEqual(len(rows), 3)

        all_values = [val for row in rows for val in row.values()]
        self.assertNotIn("other_secret_ref", content)
        self.assertNotIn("Secret other tenant transaction", content)

        self.assertIn("'=SUM(A1:A10)", all_values)
        self.assertIn("'+cmd|' /C calc'", all_values)
        self.assertIn("'-10", all_values)
        self.assertIn("'@user", all_values)
        self.assertIn('\'\t=HYPERLINK("http://evil.example")', all_values)

        # Verify expected columns & values on the reserve row
        reserve_row = next(r for r in rows if r["Entry Type"] == "RESERVE")
        self.assertEqual(reserve_row["Student"], "Alice Ledger")
        self.assertEqual(reserve_row["Amount"], "20.00")
        self.assertEqual(reserve_row["Settled Balance After"], "50.00")
        self.assertEqual(reserve_row["Reserved Balance After"], "20.00")
        self.assertTrue(reserve_row["Timestamp"])


class TeacherReceivingAddressViewTests(RoleTestCase):
    """Tests for Teacher Finance receiving address section, re-auth, EIP-55, and 24h cooldown."""

    ADDR_1 = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    ADDR_2 = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

    def test_receiving_address_endpoint_permissions_and_method_enforcement(self):
        url = reverse("payments:receiving_address_update")

        anon_resp = self.anon_client.post(
            url,
            {"address": self.ADDR_1, "current_password": "password123"},
        )
        self.assertEqual(anon_resp.status_code, 302)
        self.assertIn("/accounts/login/", anon_resp.url)

        student_resp = self.student_client.post(
            url,
            {"address": self.ADDR_1, "current_password": "password123"},
        )
        self.assertEqual(student_resp.status_code, 403)

        get_resp = self.teacher_client.get(url)
        self.assertEqual(get_resp.status_code, 405)

    def test_finance_tab_renders_receiving_address_section_active_and_pending_banner(
        self,
    ):
        now = timezone.now()
        activated_at = now - timedelta(days=3)
        pending_at = now + timedelta(hours=18)

        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_1,
            status=PaymentAddress.Status.ACTIVE,
            activates_at=activated_at,
        )
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_2,
            status=PaymentAddress.Status.PENDING_ACTIVATION,
            activates_at=pending_at,
        )

        response = self.teacher_client.get(reverse("payments:teacher_finance"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")

        self.assertIn("Receiving Address &amp; Payout Destination", html)
        self.assertIn("BNB Smart Chain (BEP-20)", html)
        self.assertIn(self.ADDR_1, html)
        self.assertIn(self.ADDR_2, html)
        self.assertIn('id="receivingAddressForm"', html)
        self.assertIn('id="receivingAddressInput"', html)
        self.assertIn('id="receivingAddressPassword"', html)
        self.assertIn('id="pendingReceivingAddressBanner"', html)
        self.assertIn(f'data-address-activates-at="{pending_at.isoformat()}"', html)

    def test_incorrect_password_returns_403_without_altering_database(self):
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_1,
            status=PaymentAddress.Status.ACTIVE,
            activates_at=timezone.now() - timedelta(days=1),
        )
        mail.outbox.clear()

        response = self.teacher_client.post(
            reverse("payments:receiving_address_update"),
            {"address": self.ADDR_2, "current_password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertEqual(
            PaymentAddress.objects.filter(tenant=self.tenant).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_eip55_checksum_returns_400_without_altering_database(self):
        response = self.teacher_client.post(
            reverse("payments:receiving_address_update"),
            {
                "address": "0x5aaeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
                "current_password": "password123",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        self.assertEqual(PaymentAddress.objects.filter(tenant=self.tenant).count(), 0)

    def test_first_address_submission_activates_immediately(self):
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(
                reverse("payments:receiving_address_update"),
                {
                    "address": self.ADDR_1.lower(),
                    "current_password": "password123",
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["address"], self.ADDR_1)
        self.assertEqual(data["address_status"], PaymentAddress.Status.ACTIVE)
        self.assertIn(self.ADDR_1, data["receiving_address_html"])

        addr = PaymentAddress.objects.get(tenant=self.tenant)
        self.assertEqual(addr.status, PaymentAddress.Status.ACTIVE)
        self.assertEqual(addr.address, self.ADDR_1)
        self.assertEqual(len(mail.outbox), 0)

    def test_subsequent_address_submission_creates_24h_pending_and_sends_alert(self):
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_1,
            status=PaymentAddress.Status.ACTIVE,
            activates_at=timezone.now() - timedelta(days=2),
        )

        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teacher_client.post(
                reverse("payments:receiving_address_update"),
                {
                    "address": self.ADDR_2,
                    "current_password": "password123",
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["address"], self.ADDR_2)
        self.assertEqual(
            data["address_status"], PaymentAddress.Status.PENDING_ACTIVATION
        )
        self.assertIn("pendingReceivingAddressBanner", data["receiving_address_html"])
        self.assertIn(self.ADDR_2, data["receiving_address_html"])
        self.assertIn(self.ADDR_1, data["receiving_address_html"])

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.teacher_user.email])

    def test_same_active_address_submission_returns_400(self):
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_1,
            status=PaymentAddress.Status.ACTIVE,
            activates_at=timezone.now() - timedelta(days=2),
        )
        response = self.teacher_client.post(
            reverse("payments:receiving_address_update"),
            {
                "address": self.ADDR_1,
                "current_password": "password123",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
