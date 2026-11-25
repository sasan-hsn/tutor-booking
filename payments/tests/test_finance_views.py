from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import Tenant
from accounts.tests.base import RoleTestCase
from booking.models import Booking
from payments.models import Order, Payment
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
