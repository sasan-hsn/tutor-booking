import base64
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Tenant, TermsAcceptance, User
from accounts.tests.base import RoleTestCase
from notifications.models import Notification
from payments.models import Order, PaymentAddress
from payments.services import create_topup_order, submit_topup_claim


class SubmitTopupClaimServiceTests(TestCase):
    VALID_ADDRESS = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"

    def setUp(self):
        cache.clear()
        mail.outbox.clear()

        self.teacher_user = User.objects.create_user(
            username="teacher_claim_svc",
            email="teacher_claim_svc@example.com",
            password="password123",
            role=User.Role.TEACHER,
            first_name="Mary",
            last_name="Teacher",
            is_email_verified=True,
        )
        self.tenant = Tenant.objects.get(
            teacher_profile=self.teacher_user.teacher_profile
        )

        self.other_teacher_user = User.objects.create_user(
            username="other_teacher_claim_svc",
            email="other_teacher_claim_svc@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.other_tenant = Tenant.objects.get(
            teacher_profile=self.other_teacher_user.teacher_profile
        )

        self.student_user = User.objects.create_user(
            username="student_claim_svc",
            email="student_claim_svc@example.com",
            password="password123",
            role=User.Role.STUDENT,
            first_name="Alice",
            last_name="Student",
            is_email_verified=True,
        )
        self.other_student = User.objects.create_user(
            username="other_student_claim_svc",
            email="other_student_claim_svc@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

        self.active_address = PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.VALID_ADDRESS,
            status=PaymentAddress.Status.ACTIVE,
        )

    def test_submit_topup_claim_sets_claim_fields_notification_and_email(self):
        order = create_topup_order(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("40.00"),
        )
        custom_paid_at = timezone.now() - timedelta(minutes=15)

        with self.captureOnCommitCallbacks(execute=True):
            claimed = submit_topup_claim(
                tenant=self.tenant,
                student=self.student_user,
                order=order,
                txhash="  0xdeadbeef1234  ",
                paid_at=custom_paid_at,
            )

        order.refresh_from_db()
        self.assertEqual(claimed.pk, order.pk)
        self.assertIsNotNone(order.claimed_at)
        self.assertEqual(order.claimed_txhash, "0xdeadbeef1234")
        self.assertEqual(order.claimed_paid_at, custom_paid_at)

        notif = Notification.objects.get(
            recipient=self.teacher_user,
            event_type=Notification.EventType.TOPUP_CLAIM_SUBMITTED,
        )
        self.assertEqual(notif.actor, self.student_user)
        self.assertEqual(notif.action_url, reverse("payments:teacher_finance"))
        self.assertEqual(notif.metadata.get("order_id"), order.id)
        self.assertIn("40.00", notif.message)
        self.assertIn("Alice Student", notif.message)

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.teacher_user.email])
        self.assertIn("40.00", sent.body)
        self.assertIn("0xdeadbeef1234", sent.body)

    def test_submit_topup_claim_defaults_paid_at_when_omitted(self):
        order = create_topup_order(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
        )
        before = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            claimed = submit_topup_claim(
                tenant=self.tenant,
                student=self.student_user,
                order=order,
            )
        after = timezone.now()

        self.assertIsNotNone(claimed.claimed_at)
        self.assertIsNotNone(claimed.claimed_paid_at)
        self.assertEqual(claimed.claimed_txhash, "")
        self.assertGreaterEqual(claimed.claimed_paid_at, before)
        self.assertLessEqual(claimed.claimed_paid_at, after)

    def test_submit_topup_claim_rejects_wrong_student_and_wrong_tenant(self):
        order = create_topup_order(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
        )

        with self.assertRaises(PermissionDenied):
            submit_topup_claim(
                tenant=self.tenant,
                student=self.other_student,
                order=order,
                txhash="0x1111",
            )

        with self.assertRaises(PermissionDenied):
            submit_topup_claim(
                tenant=self.other_tenant,
                student=self.student_user,
                order=order,
                txhash="0x1111",
            )

        order.refresh_from_db()
        self.assertIsNone(order.claimed_at)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_submit_topup_claim_rejects_duplicate_claim(self):
        order = create_topup_order(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("30.00"),
        )
        with self.captureOnCommitCallbacks(execute=True):
            submit_topup_claim(
                tenant=self.tenant,
                student=self.student_user,
                order=order,
                txhash="0xfirst",
            )

        with self.assertRaises(ValidationError):
            submit_topup_claim(
                tenant=self.tenant,
                student=self.student_user,
                order=order,
                txhash="0xsecond",
            )

        order.refresh_from_db()
        self.assertEqual(order.claimed_txhash, "0xfirst")
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_submit_topup_claim_rejects_expired_and_completed_orders(self):
        expired_order = Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key="claim-expired-key",
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        with self.assertRaises(ValidationError):
            submit_topup_claim(
                tenant=self.tenant,
                student=self.student_user,
                order=expired_order,
            )

        completed_order = Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
            currency="USD",
            status=Order.Status.COMPLETED,
            idempotency_key="claim-completed-key",
            expires_at=timezone.now() + timedelta(hours=12),
            completed_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            submit_topup_claim(
                tenant=self.tenant,
                student=self.student_user,
                order=completed_order,
            )

        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)


class LocalQrGeneratorTests(TestCase):
    VALID_ADDRESS = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"

    def test_render_qr_svg_and_data_uri(self):
        from payments.qr import render_qr_data_uri, render_qr_svg

        svg = render_qr_svg(self.VALID_ADDRESS)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn('viewBox="0 0 ', svg)
        self.assertIn("<path", svg)

        data_uri = render_qr_data_uri(self.VALID_ADDRESS)
        self.assertTrue(data_uri.startswith("data:image/svg+xml;base64,"))
        decoded = base64.b64decode(data_uri.split(",", 1)[1]).decode("utf-8")
        self.assertEqual(decoded, svg)


class StudentTopUpHttpViewTests(RoleTestCase):
    VALID_ADDRESS = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)
        cls.other_student = User.objects.create_user(
            username="other_topup_student",
            email="other_topup_student@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

    def setUp(self):
        super().setUp()
        cache.clear()
        mail.outbox.clear()
        self.active_address = PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.VALID_ADDRESS,
            status=PaymentAddress.Status.ACTIVE,
        )
        TermsAcceptance.objects.create(
            user=self.student_user,
            tenant=self.tenant,
            version="2026.1",
            ip_address="127.0.0.1",
        )

    def test_topup_endpoints_enforce_student_role_and_terms_gate(self):
        initiate_url = reverse("wallet:student_topup_initiate")
        self.assertEqual(initiate_url, "/wallet/topup/initiate/")

        anon_resp = self.anon_client.post(initiate_url, {"amount": "25.00"})
        self.assertEqual(anon_resp.status_code, 302)
        self.assertIn("/accounts/login/", anon_resp.url)

        teacher_resp = self.teacher_client.post(initiate_url, {"amount": "25.00"})
        self.assertEqual(teacher_resp.status_code, 403)

        TermsAcceptance.objects.filter(user=self.student_user).delete()
        unaccepted_resp = self.student_client.post(
            initiate_url,
            {"amount": "25.00"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(unaccepted_resp.status_code, 403)
        self.assertEqual(unaccepted_resp.json().get("error"), "terms_not_accepted")

    def test_topup_initiate_creates_order_and_reuses_unexpired_order(self):
        initiate_url = reverse("wallet:student_topup_initiate")

        resp1 = self.student_client.post(
            initiate_url,
            {"amount": "35.00"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.json()
        self.assertTrue(data1["success"])
        self.assertFalse(data1["reused"])
        self.assertEqual(data1["amount"], "35.00")
        self.assertEqual(data1["currency"], "USD")
        self.assertEqual(data1["address"], self.VALID_ADDRESS)
        self.assertTrue(data1["qr_data_uri"].startswith("data:image/svg+xml;base64,"))

        order = Order.objects.get(pk=data1["order_id"])
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertGreater(order.expires_at, timezone.now())

        # Re-initiating within expiry window with different amount reuses existing order
        resp2 = self.student_client.post(
            initiate_url,
            {"amount": "60.00"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2["success"])
        self.assertTrue(data2["reused"])
        self.assertEqual(data2["order_id"], order.id)
        self.assertEqual(data2["amount"], "35.00")
        self.assertEqual(
            Order.objects.filter(tenant=self.tenant, student=self.student_user).count(),
            1,
        )

        # Even if min_topup_amount rises or active address is later retired, existing order still resumes
        self.tenant.min_topup_amount = Decimal("50.00")
        self.tenant.save(update_fields=["min_topup_amount"])
        self.active_address.status = PaymentAddress.Status.RETIRED
        self.active_address.save(update_fields=["status"])

        resp3 = self.student_client.post(
            initiate_url,
            {"amount": "35.00"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp3.status_code, 200)
        self.assertTrue(resp3.json()["reused"])
        self.assertEqual(resp3.json()["order_id"], order.id)
        self.assertEqual(resp3.json()["amount"], "35.00")

    def test_topup_initiate_validates_min_amount_and_inactive_address(self):
        initiate_url = reverse("wallet:student_topup_initiate")

        low_resp = self.student_client.post(
            initiate_url,
            {"amount": "19.99"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(low_resp.status_code, 400)
        low_data = low_resp.json()
        self.assertFalse(low_data["success"])
        self.assertIn("20.00", low_data["error"])
        self.assertEqual(Order.objects.count(), 0)

        self.active_address.status = PaymentAddress.Status.RETIRED
        self.active_address.save(update_fields=["status"])

        no_addr_resp = self.student_client.post(
            initiate_url,
            {"amount": "25.00"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(no_addr_resp.status_code, 400)
        self.assertIn("active payment address", no_addr_resp.json()["error"].lower())
        self.assertEqual(Order.objects.count(), 0)

    def test_topup_claim_updates_order_notifies_teacher_and_surfaces_in_review_queue(
        self,
    ):
        order = create_topup_order(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("45.00"),
        )
        claim_url = reverse("wallet:student_topup_claim", kwargs={"order_id": order.id})
        self.assertEqual(claim_url, f"/wallet/topup/{order.id}/claim/")

        paid_at_local_str = (timezone.now() - timedelta(minutes=10)).strftime(
            "%Y-%m-%dT%H:%M"
        )
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.student_client.post(
                claim_url,
                {
                    "txhash": "0xclaimabc123",
                    "paid_at": paid_at_local_str,
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["success"])
        self.assertEqual(
            payload["message"],
            "Your payment claim has been submitted. The teacher will verify the deposit and credit your account shortly.",
        )

        order.refresh_from_db()
        self.assertIsNotNone(order.claimed_at)
        self.assertEqual(order.claimed_txhash, "0xclaimabc123")
        self.assertIsNotNone(order.claimed_paid_at)

        self.assertEqual(
            Notification.objects.filter(
                recipient=self.teacher_user,
                event_type=Notification.EventType.TOPUP_CLAIM_SUBMITTED,
            ).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 1)

        finance_resp = self.teacher_client.get(reverse("payments:teacher_finance"))
        self.assertEqual(finance_resp.status_code, 200)
        self.assertContains(finance_resp, "0xclaimabc123")
        self.assertContains(finance_resp, 'data-amount="45.00"')
        self.assertContains(finance_resp, f'data-student-id="{self.student_user.id}"')

    def test_topup_claim_rejects_other_student_duplicate_and_expired_order(self):
        order = create_topup_order(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
        )
        claim_url = reverse("wallet:student_topup_claim", kwargs={"order_id": order.id})

        TermsAcceptance.objects.create(
            user=self.other_student,
            tenant=self.tenant,
            version="2026.1",
            ip_address="127.0.0.1",
        )
        other_client = self.client_class()
        other_client.force_login(self.other_student)
        forbidden_resp = other_client.post(
            claim_url,
            {"txhash": "0xhijack"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(forbidden_resp.status_code, 403)

        first_resp = self.student_client.post(
            claim_url,
            {"txhash": "0xfirst"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(first_resp.status_code, 200)

        dup_resp = self.student_client.post(
            claim_url,
            {"txhash": "0xsecond"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(dup_resp.status_code, 409)

        expired_order = Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key="http-expired-claim",
            expires_at=timezone.now() - timedelta(minutes=5),
        )
        expired_resp = self.student_client.post(
            reverse(
                "wallet:student_topup_claim", kwargs={"order_id": expired_order.id}
            ),
            {"txhash": "0xexpired"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(expired_resp.status_code, 409)

    def test_wallet_page_and_navbar_trigger_render_modal_and_inactive_address_state(
        self,
    ):
        resp = self.student_client.get(reverse("wallet:student_wallet"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="navbarWalletTopupBtn"')
        self.assertContains(resp, 'href="/wallet/?topup=1"')
        self.assertContains(resp, 'id="topUpModal"')
        self.assertContains(resp, 'id="bep20AckCheckbox"')
        self.assertContains(resp, "topup-bep20-gate")
        self.assertContains(resp, 'id="topupDepositLockOverlay"')
        self.assertContains(resp, "topup-deposit-blurred")
        self.assertContains(
            resp,
            'id="showClaimFormBtn" disabled',
        )
        self.assertContains(
            resp,
            "I confirm I will send USDT on the BNB Smart Chain (BEP-20) network only.",
        )
        self.assertContains(resp, 'data-has-active-address="true"')
        # Address must NOT be exposed in rendered HTML before BEP-20 acknowledgment
        self.assertNotContains(resp, self.VALID_ADDRESS)
        self.assertNotContains(resp, 'id="topupInactiveAddressAlert"')

        # Retire active address -> modal disables order creation & shows informative alert
        self.active_address.status = PaymentAddress.Status.RETIRED
        self.active_address.save(update_fields=["status"])

        inactive_resp = self.student_client.get(reverse("wallet:student_wallet"))
        self.assertEqual(inactive_resp.status_code, 200)
        self.assertContains(inactive_resp, 'data-has-active-address="false"')
        self.assertContains(inactive_resp, 'id="topupInactiveAddressAlert"')
        self.assertContains(inactive_resp, "Top-ups temporarily unavailable.")
