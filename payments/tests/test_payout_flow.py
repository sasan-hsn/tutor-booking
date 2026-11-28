import hashlib
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core import mail
from django.db import transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Tenant, User
from accounts.tests.base import RoleTestCase
from notifications.models import Notification
from payments.models import Payout
from payments.services import (
    create_payout_request,
    mark_payout_processing,
    verify_and_reserve_payout,
)
from wallet.models import LedgerEntry
from wallet.services import credit_wallet, get_or_create_wallet, reserve_funds

CONFIRM_URL_RE = re.compile(
    r"https?://[^\s\"']+/wallet/payouts/confirm/([A-Za-z0-9_-]+)/"
)


@override_settings(SITE_URL="https://tutor.example.com")
class StudentPayoutFlowTests(RoleTestCase):
    """End-to-end HTTP and domain tests for Issue #301 two-stage student payout flow."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)
        cls.other_student = User.objects.create_user(
            username="other_payout_student",
            email="other_payout_student@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

    def setUp(self):
        super().setUp()
        mail.outbox.clear()
        self.wallet = get_or_create_wallet(
            tenant=self.tenant,
            user=self.student_user,
            currency=self.tenant.default_currency,
        )

    def _credit(self, amount: Decimal = Decimal("100.00"), key: str = "credit-100"):
        return credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=amount,
            idempotency_key=key,
            reference_id=f"ref_{key}",
            description="Test deposit credit",
        )

    def _extract_token_from_email(self, email_message) -> str:
        match = CONFIRM_URL_RE.search(email_message.body)
        self.assertIsNotNone(
            match, f"Confirmation URL not found in email body:\n{email_message.body}"
        )
        return match.group(1)

    def test_request_refund_button_visibility_and_modal_copy(self):
        wallet_url = reverse("wallet:student_wallet")

        # Zero balance -> Request Refund trigger is hidden
        resp_zero = self.student_client.get(wallet_url)
        self.assertEqual(resp_zero.status_code, 200)
        self.assertNotContains(resp_zero, 'id="requestRefundBtn"')

        # Positive available balance -> subtle/secondary trigger visible + modal disclaimer
        self._credit(Decimal("50.00"), "credit-vis-50")
        resp_pos = self.student_client.get(wallet_url)
        self.assertEqual(resp_pos.status_code, 200)
        self.assertContains(resp_pos, 'id="requestRefundBtn"')
        self.assertContains(resp_pos, "manually reviewed by the teacher")
        self.assertContains(resp_pos, "fees may be deducted")

    def test_stage1_request_creation_sends_absolute_email_link_and_leaves_balance_unchanged(
        self,
    ):
        self._credit(Decimal("50.00"), "credit-stage1-50")
        request_url = reverse("wallet:student_payout_request")
        self.assertEqual(request_url, "/wallet/payouts/request/")

        # Small amount ($0.50) must be accepted (no unrequested minimum payout threshold)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.student_client.post(
                request_url,
                {
                    "amount": "25.50",
                    "destination": "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
                    "notes": "Unused lesson balance",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["status"], Payout.Status.PENDING_CONFIRMATION)
        self.assertEqual(
            payload["message"],
            "We have sent a confirmation link to your email. You must confirm this request within 24 hours.",
        )

        payout = Payout.objects.get(pk=payload["payout_id"])
        self.assertEqual(payout.status, Payout.Status.PENDING_CONFIRMATION)
        self.assertEqual(payout.amount_gross, Decimal("25.50"))
        self.assertEqual(
            payout.destination, "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
        )
        self.assertEqual(payout.notes, "Unused lesson balance")
        self.assertIsNotNone(payout.confirmation_token_hash)

        expected_expiry = timezone.now() + timedelta(
            hours=getattr(settings, "PAYOUT_CONFIRMATION_HOURS", 24)
        )
        self.assertAlmostEqual(
            payout.confirmation_expires_at.timestamp(),
            expected_expiry.timestamp(),
            delta=10,
        )

        # Balance unchanged in Stage 1
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("50.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("50.00"))
        self.assertEqual(
            LedgerEntry.objects.filter(
                wallet=self.wallet, entry_type=LedgerEntry.EntryType.PAYOUT_HOLD
            ).count(),
            0,
        )

        # Email dispatched with full absolute confirmation URL and SHA-256 hash matches
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.student_user.email])
        self.assertIn("https://tutor.example.com/wallet/payouts/confirm/", sent.body)
        raw_token = self._extract_token_from_email(sent)
        self.assertNotEqual(raw_token, payout.confirmation_token_hash)
        self.assertEqual(
            hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            payout.confirmation_token_hash,
        )

    def test_stage1_validation_rejects_over_withdrawal_zero_negative_and_empty_destination(
        self,
    ):
        self._credit(Decimal("40.00"), "credit-val-40")
        request_url = reverse("wallet:student_payout_request")

        bad_payloads = [
            {"amount": "40.01", "destination": "0xabc", "notes": ""},
            {"amount": "0.00", "destination": "0xabc", "notes": ""},
            {"amount": "-10.00", "destination": "0xabc", "notes": ""},
            {"amount": "10.00", "destination": "   ", "notes": ""},
        ]
        for data in bad_payloads:
            with self.subTest(data=data):
                resp = self.student_client.post(
                    request_url,
                    data,
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )
                self.assertEqual(resp.status_code, 400)
                self.assertFalse(resp.json()["success"])

        self.assertEqual(Payout.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_stage1_blocks_frozen_wallet(self):
        self._credit(Decimal("40.00"), "credit-frozen-40")
        self.wallet.is_frozen = True
        self.wallet.save(update_fields=["is_frozen"])

        resp = self.student_client.post(
            reverse("wallet:student_payout_request"),
            {"amount": "10.00", "destination": "0xabc", "notes": ""},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["success"])
        self.assertEqual(Payout.objects.count(), 0)

    def test_stage2_confirmation_get_preview_is_safe_and_post_confirms_with_hold_and_teacher_notif(
        self,
    ):
        self._credit(Decimal("80.00"), "credit-stage2-80")
        payout = create_payout_request(
            tenant=self.tenant,
            student=self.student_user,
            amount_gross=Decimal("30.00"),
            destination="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
            notes="Course completed",
        )
        raw_token = payout.raw_token
        confirm_url = reverse(
            "wallet:student_payout_confirm", kwargs={"raw_token": raw_token}
        )
        self.assertEqual(confirm_url, f"/wallet/payouts/confirm/{raw_token}/")

        # Unauthenticated GET redirects to login with ?next=
        anon_resp = self.client.get(confirm_url)
        self.assertEqual(anon_resp.status_code, 302)
        self.assertIn(confirm_url, anon_resp["Location"])

        # GET by owning student renders preview WITHOUT mutating state (scanner safe)
        get_resp = self.student_client.get(confirm_url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertContains(get_resp, "Confirm Your Refund Request")
        self.assertContains(get_resp, "$30.00")
        payout.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.PENDING_CONFIRMATION)
        self.assertIsNone(payout.confirmation_used_at)
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(Notification.objects.count(), 0)

        # POST confirms, transitions to REQUESTED, places PAYOUT_HOLD, notifies teacher on commit
        with self.captureOnCommitCallbacks(execute=True):
            post_resp = self.student_client.post(confirm_url)

        self.assertEqual(post_resp.status_code, 200)
        self.assertContains(post_resp, "Refund Request Confirmed")

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.REQUESTED)
        self.assertIsNotNone(payout.confirmation_used_at)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("30.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("50.00"))

        hold_entries = LedgerEntry.objects.filter(
            wallet=self.wallet,
            entry_type=LedgerEntry.EntryType.PAYOUT_HOLD,
        )
        self.assertEqual(hold_entries.count(), 1)
        self.assertEqual(hold_entries.first().amount, Decimal("30.00"))

        notif = Notification.objects.get(
            recipient=self.teacher_user,
            event_type=Notification.EventType.PAYOUT_REQUESTED,
        )
        self.assertEqual(notif.action_url, reverse("payments:teacher_finance"))
        self.assertEqual(notif.metadata.get("payout_id"), payout.id)

        # Replay POST/GET does not double-hold
        replay_resp = self.student_client.post(confirm_url)
        self.assertEqual(replay_resp.status_code, 200)
        self.assertContains(replay_resp, "Refund Already Confirmed")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("30.00"))
        self.assertEqual(
            LedgerEntry.objects.filter(
                wallet=self.wallet, entry_type=LedgerEntry.EntryType.PAYOUT_HOLD
            ).count(),
            1,
        )

    def test_stage2_expired_and_invalid_tokens_leave_balance_and_status_untouched(self):
        self._credit(Decimal("60.00"), "credit-exp-60")
        payout = create_payout_request(
            tenant=self.tenant,
            student=self.student_user,
            amount_gross=Decimal("20.00"),
            destination="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        )
        raw_token = payout.raw_token
        payout.confirmation_expires_at = timezone.now() - timedelta(minutes=5)
        payout.save(update_fields=["confirmation_expires_at"])

        confirm_url = reverse(
            "wallet:student_payout_confirm", kwargs={"raw_token": raw_token}
        )

        get_resp = self.student_client.get(confirm_url)
        self.assertEqual(get_resp.status_code, 400)
        self.assertContains(get_resp, "Confirmation Link Expired", status_code=400)

        post_resp = self.student_client.post(confirm_url)
        self.assertEqual(post_resp.status_code, 400)
        self.assertContains(post_resp, "Confirmation Link Expired", status_code=400)

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.PENDING_CONFIRMATION)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("60.00"))

        # Invalid token
        bad_url = reverse(
            "wallet:student_payout_confirm", kwargs={"raw_token": "invalid-token-123"}
        )
        bad_resp = self.student_client.post(bad_url)
        self.assertEqual(bad_resp.status_code, 400)
        self.assertContains(bad_resp, "Invalid or Unavailable Link", status_code=400)

    def test_stage2_balance_drain_race_fails_safely_without_hold(self):
        self._credit(Decimal("50.00"), "credit-race-50")
        payout = create_payout_request(
            tenant=self.tenant,
            student=self.student_user,
            amount_gross=Decimal("40.00"),
            destination="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        )
        confirm_url = reverse(
            "wallet:student_payout_confirm", kwargs={"raw_token": payout.raw_token}
        )

        # Student spends $30.00 on a lesson between Stage 1 and Stage 2, leaving $20.00 available
        reserve_funds(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("30.00"),
            idempotency_key="booking-drain-30",
            reference_id="booking_999",
            description="Lesson reservation between Stage 1 and Stage 2",
        )

        with self.captureOnCommitCallbacks(execute=True):
            post_resp = self.student_client.post(confirm_url)

        self.assertEqual(post_resp.status_code, 400)
        self.assertContains(
            post_resp, "Insufficient Available Balance", status_code=400
        )

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.CANCELLED)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("30.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("20.00"))
        self.assertEqual(
            LedgerEntry.objects.filter(
                wallet=self.wallet, entry_type=LedgerEntry.EntryType.PAYOUT_HOLD
            ).count(),
            0,
        )
        self.assertEqual(Notification.objects.count(), 0)

    def test_stage2_blocks_cross_user_access(self):
        self._credit(Decimal("50.00"), "credit-idor-confirm-50")
        payout = create_payout_request(
            tenant=self.tenant,
            student=self.student_user,
            amount_gross=Decimal("20.00"),
            destination="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        )
        confirm_url = reverse(
            "wallet:student_payout_confirm", kwargs={"raw_token": payout.raw_token}
        )

        self.client.force_login(self.other_student)
        get_resp = self.client.get(confirm_url)
        self.assertEqual(get_resp.status_code, 403)
        post_resp = self.client.post(confirm_url)
        self.assertEqual(post_resp.status_code, 403)

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.PENDING_CONFIRMATION)

    def test_student_self_cancellation_pending_confirmation_and_requested(self):
        self._credit(Decimal("100.00"), "credit-cancel-100")

        # 1. Cancel PENDING_CONFIRMATION payout (no hold to release)
        unconfirmed = create_payout_request(
            tenant=self.tenant,
            student=self.student_user,
            amount_gross=Decimal("20.00"),
            destination="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        )
        cancel_unconfirmed_url = reverse(
            "wallet:student_payout_cancel", kwargs={"payout_id": unconfirmed.id}
        )
        resp1 = self.student_client.post(
            cancel_unconfirmed_url,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp1.json()["status"], Payout.Status.CANCELLED)
        unconfirmed.refresh_from_db()
        self.assertEqual(unconfirmed.status, Payout.Status.CANCELLED)
        self.assertEqual(
            LedgerEntry.objects.filter(
                wallet=self.wallet, entry_type=LedgerEntry.EntryType.PAYOUT_RELEASE
            ).count(),
            0,
        )

        # 2. Cancel REQUESTED payout (releases PAYOUT_HOLD and restores available_balance)
        requested = create_payout_request(
            tenant=self.tenant,
            student=self.student_user,
            amount_gross=Decimal("35.00"),
            destination="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        )
        verify_and_reserve_payout(
            tenant=self.tenant,
            payout=requested,
            raw_token=requested.raw_token,
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("35.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("65.00"))

        cancel_requested_url = reverse(
            "wallet:student_payout_cancel", kwargs={"payout_id": requested.id}
        )
        resp2 = self.student_client.post(
            cancel_requested_url,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2["success"])
        self.assertEqual(data2["status"], Payout.Status.CANCELLED)
        self.assertEqual(data2["available_balance"], "100.00")
        self.assertEqual(data2["reserved_balance"], "0.00")

        requested.refresh_from_db()
        self.assertEqual(requested.status, Payout.Status.CANCELLED)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("100.00"))
        self.assertEqual(
            LedgerEntry.objects.filter(
                wallet=self.wallet, entry_type=LedgerEntry.EntryType.PAYOUT_RELEASE
            ).count(),
            1,
        )

    def test_student_cancellation_blocks_processing_and_idor_and_enforces_post_only(
        self,
    ):
        self._credit(Decimal("50.00"), "credit-block-50")
        payout = create_payout_request(
            tenant=self.tenant,
            student=self.student_user,
            amount_gross=Decimal("25.00"),
            destination="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        )
        verify_and_reserve_payout(
            tenant=self.tenant,
            payout=payout,
            raw_token=payout.raw_token,
        )
        cancel_url = reverse(
            "wallet:student_payout_cancel", kwargs={"payout_id": payout.id}
        )

        # GET not allowed (POST-only)
        get_resp = self.student_client.get(cancel_url)
        self.assertEqual(get_resp.status_code, 405)

        # IDOR: another student cannot cancel
        self.client.force_login(self.other_student)
        idor_resp = self.client.post(
            cancel_url,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(idor_resp.status_code, 403)
        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.REQUESTED)

        # Teacher marks PROCESSING -> student cancellation returns 409 Conflict
        mark_payout_processing(
            tenant=self.tenant,
            payout=payout,
            admin_user=self.teacher_user,
        )
        proc_resp = self.student_client.post(
            cancel_url,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(proc_resp.status_code, 409)
        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.PROCESSING)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("25.00"))

    def test_verify_and_reserve_payout_does_not_notify_teacher_on_rollback(self):
        self._credit(Decimal("50.00"), "credit-rollback-50")
        payout = create_payout_request(
            tenant=self.tenant,
            student=self.student_user,
            amount_gross=Decimal("20.00"),
            destination="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        )

        class ForcedRollbackError(Exception):
            pass

        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(ForcedRollbackError):
                with transaction.atomic():
                    verify_and_reserve_payout(
                        tenant=self.tenant,
                        payout=payout,
                        raw_token=payout.raw_token,
                    )
                    raise ForcedRollbackError("Simulated rollback after confirmation")

        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.PAYOUT_REQUESTED
            ).count(),
            0,
        )
        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.PENDING_CONFIRMATION)
