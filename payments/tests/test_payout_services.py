import hashlib
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import Tenant, User
from payments.exceptions import (
    PayoutConflictError,
    PayoutInsufficientBalanceError,
)
from payments.models import Payout
from payments.services import (
    cancel_payout_request,
    create_payout_request,
    mark_payout_processing,
    release_payout,
    settle_payout,
    verify_and_reserve_payout,
)
from wallet.exceptions import WalletFrozenError
from wallet.models import LedgerEntry
from wallet.services import credit_wallet, get_or_create_wallet, reserve_funds


class CreatePayoutRequestTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_payout_test",
            password="testpassword",
            role=User.Role.TEACHER,
            email="teacher@example.com",
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)

        self.student = User.objects.create_user(
            username="student_payout_test",
            password="testpassword",
            role=User.Role.STUDENT,
            email="student@example.com",
            first_name="Jane",
        )

        self.wallet = get_or_create_wallet(
            tenant=self.tenant,
            user=self.student,
            currency="USD",
        )

        # Credit wallet with $50.00
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="setup-credit-50",
            reference_id="ref-setup-50",
            description="Initial top-up",
        )

    def test_create_payout_request_success(self):
        destination = "0x71C8000000000000000000000000000000000000"
        amount = Decimal("25.00")

        payout = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=amount,
            destination=destination,
            provider="crypto_usdt",
        )

        self.assertIsNotNone(payout.pk)
        self.assertEqual(payout.status, Payout.Status.PENDING_CONFIRMATION)
        self.assertEqual(payout.amount_gross, amount)
        self.assertEqual(payout.amount_net, amount)
        self.assertEqual(payout.fee_deducted, Decimal("0.00"))
        self.assertEqual(payout.destination, destination)
        self.assertEqual(payout.provider, "crypto_usdt")
        self.assertEqual(payout.tenant, self.tenant)
        self.assertEqual(payout.student, self.student)

        # Token verification
        raw_token = getattr(payout, "raw_token", None)
        self.assertIsNotNone(raw_token)
        computed_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        self.assertEqual(payout.confirmation_token_hash, computed_hash)

        # 24h expiration
        now = timezone.now()
        expected_expiry = now + timedelta(hours=24)
        self.assertAlmostEqual(
            payout.confirmation_expires_at.timestamp(),
            expected_expiry.timestamp(),
            delta=5,
        )

        # Acceptance Criteria: No ledger hold is placed yet
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("50.00"))
        self.assertEqual(
            LedgerEntry.objects.filter(
                wallet=self.wallet,
                entry_type=LedgerEntry.EntryType.PAYOUT_HOLD,
            ).count(),
            0,
        )

        # Basic confirmation email dispatched
        self.assertEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[0]
        self.assertEqual(sent_email.to, [self.student.email])
        self.assertIn(raw_token, sent_email.body)
        self.assertIn("25.00", sent_email.body)

    def test_create_payout_request_insufficient_balance(self):
        # Student has $50.00, requests $60.00
        with self.assertRaises((PayoutInsufficientBalanceError, ValidationError)):
            create_payout_request(
                tenant=self.tenant,
                student=self.student,
                amount_gross=Decimal("60.00"),
                destination="0x71C8000000000000000000000000000000000000",
            )
        self.assertEqual(Payout.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_create_payout_request_invalid_amount(self):
        for bad_amount in [Decimal("0.00"), Decimal("-10.00")]:
            with self.subTest(amount=bad_amount):
                with self.assertRaises(ValidationError):
                    create_payout_request(
                        tenant=self.tenant,
                        student=self.student,
                        amount_gross=bad_amount,
                        destination="0x71C8000000000000000000000000000000000000",
                    )

    def test_create_payout_request_empty_destination(self):
        with self.assertRaises(ValidationError):
            create_payout_request(
                tenant=self.tenant,
                student=self.student,
                amount_gross=Decimal("20.00"),
                destination="   ",
            )


class VerifyAndReservePayoutTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_verify_test",
            password="testpassword",
            role=User.Role.TEACHER,
            email="teacher_v@example.com",
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)

        self.student = User.objects.create_user(
            username="student_verify_test",
            password="testpassword",
            role=User.Role.STUDENT,
            email="student_v@example.com",
            first_name="Jane",
        )

        self.wallet = get_or_create_wallet(
            tenant=self.tenant,
            user=self.student,
            currency="USD",
        )

        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="setup-credit-verify-50",
            reference_id="ref-verify-50",
            description="Initial top-up for verify tests",
        )

        self.payout = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=Decimal("25.00"),
            destination="0x71C8000000000000000000000000000000000000",
        )
        self.raw_token = self.payout.raw_token

    def test_verify_and_reserve_payout_success(self):
        entry = verify_and_reserve_payout(
            tenant=self.tenant,
            payout=self.payout,
            raw_token=self.raw_token,
        )

        self.assertIsNotNone(entry.pk)
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.PAYOUT_HOLD)
        self.assertEqual(entry.amount, Decimal("25.00"))
        self.assertEqual(entry.settled_after, Decimal("50.00"))
        self.assertEqual(entry.reserved_after, Decimal("25.00"))
        self.assertEqual(
            entry.idempotency_key,
            f"tenant:{self.tenant.id}:payout:{self.payout.id}:hold",
        )

        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.REQUESTED)
        self.assertIsNotNone(self.payout.confirmation_used_at)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("25.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("25.00"))

    def test_verify_and_reserve_payout_expired_token(self):
        self.payout.confirmation_expires_at = timezone.now() - timedelta(minutes=1)
        self.payout.save(update_fields=["confirmation_expires_at"])

        with self.assertRaises(ValidationError):
            verify_and_reserve_payout(
                tenant=self.tenant,
                payout=self.payout,
                raw_token=self.raw_token,
            )

    def test_verify_and_reserve_payout_invalid_token(self):
        with self.assertRaises(ValidationError):
            verify_and_reserve_payout(
                tenant=self.tenant,
                payout=self.payout,
                raw_token="wrong_token_value",
            )

        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.PENDING_CONFIRMATION)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_verify_and_reserve_payout_recheck_balance_failure_commits_cancelled(self):
        # Simulate balance drop: student reserves funds elsewhere, leaving only $10 available
        reserve_funds(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("40.00"),
            idempotency_key="competing-reservation-40",
            reference_id="competing-ref",
            description="Competing reservation",
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal("10.00"))

        with self.assertRaises((PayoutInsufficientBalanceError, ValidationError)):
            verify_and_reserve_payout(
                tenant=self.tenant,
                payout=self.payout,
                raw_token=self.raw_token,
            )

        # Guardrail 1: CANCELLED status must be committed to the database despite the exception
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.CANCELLED)

        # No hold placed for this payout
        self.assertEqual(
            LedgerEntry.objects.filter(
                wallet=self.wallet,
                entry_type=LedgerEntry.EntryType.PAYOUT_HOLD,
            ).count(),
            0,
        )

    def test_verify_and_reserve_payout_frozen_wallet(self):
        self.wallet.is_frozen = True
        self.wallet.save(update_fields=["is_frozen"])

        with self.assertRaises(WalletFrozenError):
            verify_and_reserve_payout(
                tenant=self.tenant,
                payout=self.payout,
                raw_token=self.raw_token,
            )

        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.PENDING_CONFIRMATION)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_verify_and_reserve_payout_wrong_initial_status(self):
        self.payout.status = Payout.Status.REQUESTED
        self.payout.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            verify_and_reserve_payout(
                tenant=self.tenant,
                payout=self.payout,
                raw_token=self.raw_token,
            )


class CancelPayoutRequestTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_cancel_test",
            password="testpassword",
            role=User.Role.TEACHER,
            email="teacher_c@example.com",
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)

        self.student = User.objects.create_user(
            username="student_cancel_test",
            password="testpassword",
            role=User.Role.STUDENT,
            email="student_c@example.com",
            first_name="Jane",
        )

        self.wallet = get_or_create_wallet(
            tenant=self.tenant,
            user=self.student,
            currency="USD",
        )

        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="setup-credit-cancel-50",
            reference_id="ref-cancel-50",
            description="Initial top-up for cancel tests",
        )

        self.payout = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=Decimal("25.00"),
            destination="0x71C8000000000000000000000000000000000000",
        )

        # Transition to REQUESTED with PAYOUT_HOLD
        verify_and_reserve_payout(
            tenant=self.tenant,
            payout=self.payout,
            raw_token=self.payout.raw_token,
        )

    def test_cancel_payout_request_success(self):
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("25.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("25.00"))

        entry = cancel_payout_request(
            tenant=self.tenant,
            payout=self.payout,
            user=self.student,
        )

        self.assertIsNotNone(entry.pk)
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.PAYOUT_RELEASE)
        self.assertEqual(entry.amount, Decimal("25.00"))
        self.assertEqual(entry.settled_after, Decimal("50.00"))
        self.assertEqual(entry.reserved_after, Decimal("0.00"))
        self.assertEqual(
            entry.idempotency_key,
            f"tenant:{self.tenant.id}:payout:{self.payout.id}:release",
        )

        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.CANCELLED)

        # Available balance is restored
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("50.00"))

    def test_cancel_payout_request_when_processing_raises_409_conflict(self):
        self.payout.status = Payout.Status.PROCESSING
        self.payout.save(update_fields=["status"])

        with self.assertRaises(PayoutConflictError) as ctx:
            cancel_payout_request(
                tenant=self.tenant,
                payout=self.payout,
                user=self.student,
            )

        self.assertEqual(ctx.exception.status_code, 409)
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.PROCESSING)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("25.00"))

    def test_cancel_payout_request_when_pending_confirmation_raises_409_conflict(self):
        # Create unverified payout
        unconfirmed = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=Decimal("10.00"),
            destination="0x71C8000000000000000000000000000000000000",
        )
        with self.assertRaises(PayoutConflictError) as ctx:
            cancel_payout_request(
                tenant=self.tenant,
                payout=unconfirmed,
                user=self.student,
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_cancel_payout_request_when_already_completed_raises_409_conflict(self):
        self.payout.status = Payout.Status.COMPLETED
        self.payout.save(update_fields=["status"])

        with self.assertRaises(PayoutConflictError) as ctx:
            cancel_payout_request(
                tenant=self.tenant,
                payout=self.payout,
                user=self.student,
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_cancel_payout_request_unauthorized_user(self):
        other_student = User.objects.create_user(
            username="other_student_cancel",
            password="testpassword",
            role=User.Role.STUDENT,
        )
        with self.assertRaises(PermissionDenied):
            cancel_payout_request(
                tenant=self.tenant,
                payout=self.payout,
                user=other_student,
            )


class TeacherPayoutWorkflowTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_wf_test",
            password="testpassword",
            role=User.Role.TEACHER,
            email="teacher_wf@example.com",
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)

        self.student = User.objects.create_user(
            username="student_wf_test",
            password="testpassword",
            role=User.Role.STUDENT,
            email="student_wf@example.com",
            first_name="Jane",
        )

        self.wallet = get_or_create_wallet(
            tenant=self.tenant,
            user=self.student,
            currency="USD",
        )

        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="setup-credit-wf-50",
            reference_id="ref-wf-50",
            description="Initial top-up for workflow tests",
        )

        self.payout = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=Decimal("25.00"),
            destination="0x71C8000000000000000000000000000000000000",
        )

        # Transition to REQUESTED with PAYOUT_HOLD
        verify_and_reserve_payout(
            tenant=self.tenant,
            payout=self.payout,
            raw_token=self.payout.raw_token,
        )

    def test_mark_payout_processing_success(self):
        payout = mark_payout_processing(
            tenant=self.tenant,
            payout=self.payout,
            admin_user=self.teacher,
        )

        self.assertEqual(payout.status, Payout.Status.PROCESSING)
        self.assertIsNotNone(payout.processed_at)

        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.PROCESSING)
        self.assertIsNotNone(self.payout.processed_at)

        # Hold balance is unaffected
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("25.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("25.00"))

    def test_mark_payout_processing_invalid_initial_status(self):
        self.payout.status = Payout.Status.COMPLETED
        self.payout.save(update_fields=["status"])

        with self.assertRaises(PayoutConflictError):
            mark_payout_processing(
                tenant=self.tenant,
                payout=self.payout,
                admin_user=self.teacher,
            )

    def test_settle_payout_success(self):
        # Must be in PROCESSING first
        mark_payout_processing(
            tenant=self.tenant,
            payout=self.payout,
            admin_user=self.teacher,
        )

        txhash = "0x9876543210abcdef9876543210abcdef9876543210abcdef9876543210abcdef"
        entry = settle_payout(
            tenant=self.tenant,
            payout=self.payout,
            external_ref=txhash,
            admin_user=self.teacher,
        )

        self.assertIsNotNone(entry.pk)
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.PAYOUT_SETTLE)
        self.assertEqual(entry.amount, Decimal("25.00"))
        # Settled was 50, decremented by 25 -> 25
        self.assertEqual(entry.settled_after, Decimal("25.00"))
        # Reserved was 25, decremented by 25 -> 0
        self.assertEqual(entry.reserved_after, Decimal("0.00"))
        self.assertEqual(
            entry.idempotency_key,
            f"tenant:{self.tenant.id}:payout:{self.payout.id}:settle",
        )

        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.COMPLETED)
        self.assertEqual(self.payout.external_ref, txhash)

        # Wallet balances updated: settled decreases, reserved decreases
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("25.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("25.00"))

    def test_settle_payout_invalid_status_when_not_processing(self):
        # Currently in REQUESTED, not PROCESSING
        with self.assertRaises(PayoutConflictError):
            settle_payout(
                tenant=self.tenant,
                payout=self.payout,
                external_ref="0xabc123",
            )

    def test_settle_payout_empty_external_ref(self):
        mark_payout_processing(
            tenant=self.tenant,
            payout=self.payout,
        )
        with self.assertRaises(ValidationError):
            settle_payout(
                tenant=self.tenant,
                payout=self.payout,
                external_ref="   ",
            )

    def test_release_payout_from_requested(self):
        entry = release_payout(
            tenant=self.tenant,
            payout=self.payout,
            reason="Invalid address provided",
            admin_user=self.teacher,
        )

        self.assertIsNotNone(entry.pk)
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.PAYOUT_RELEASE)
        self.assertEqual(entry.amount, Decimal("25.00"))
        self.assertEqual(entry.settled_after, Decimal("50.00"))
        self.assertEqual(entry.reserved_after, Decimal("0.00"))

        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.REJECTED)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("50.00"))

    def test_release_payout_from_processing(self):
        mark_payout_processing(
            tenant=self.tenant,
            payout=self.payout,
        )

        entry = release_payout(
            tenant=self.tenant,
            payout=self.payout,
            reason="Transaction failed on blockchain",
            admin_user=self.teacher,
        )

        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.PAYOUT_RELEASE)
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.REJECTED)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("50.00"))

    def test_release_payout_invalid_status_when_completed(self):
        mark_payout_processing(
            tenant=self.tenant,
            payout=self.payout,
        )
        settle_payout(
            tenant=self.tenant,
            payout=self.payout,
            external_ref="0xabc123",
        )

        with self.assertRaises(PayoutConflictError):
            release_payout(
                tenant=self.tenant,
                payout=self.payout,
                reason="Cannot reject settled payout",
            )

    def test_mark_payout_processing_sends_email_on_commit(self):
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            mark_payout_processing(
                tenant=self.tenant,
                payout=self.payout,
                admin_user=self.teacher,
            )

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.student.email])
        self.assertIn("processing", sent.subject.lower() + " " + sent.body.lower())
        self.assertIn("25.00", sent.body)

    def test_settle_payout_sends_email_on_commit(self):
        mark_payout_processing(
            tenant=self.tenant,
            payout=self.payout,
            admin_user=self.teacher,
        )
        mail.outbox.clear()
        txhash = "0xsettledemailhash1234567890abcdef"
        with self.captureOnCommitCallbacks(execute=True):
            settle_payout(
                tenant=self.tenant,
                payout=self.payout,
                external_ref=txhash,
                admin_user=self.teacher,
            )

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.student.email])
        self.assertIn(txhash, sent.body)
        self.assertIn("25.00", sent.body)

    def test_release_payout_sends_email_with_reason_on_commit(self):
        mail.outbox.clear()
        reason = "Destination address is not BEP-20 compatible."
        with self.captureOnCommitCallbacks(execute=True):
            release_payout(
                tenant=self.tenant,
                payout=self.payout,
                reason=reason,
                admin_user=self.teacher,
            )

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.student.email])
        self.assertIn(reason, sent.body)
        self.assertIn("25.00", sent.body)

    def test_settle_payout_duplicate_external_ref_raises_validation_error(self):
        mark_payout_processing(
            tenant=self.tenant,
            payout=self.payout,
            admin_user=self.teacher,
        )
        txhash = "0xduplicatetxhash0001"
        settle_payout(
            tenant=self.tenant,
            payout=self.payout,
            external_ref=txhash,
            admin_user=self.teacher,
        )

        second_payout = create_payout_request(
            tenant=self.tenant,
            student=self.student,
            amount_gross=Decimal("10.00"),
            destination="0x71C8000000000000000000000000000000000000",
        )
        verify_and_reserve_payout(
            tenant=self.tenant,
            payout=second_payout,
            raw_token=second_payout.raw_token,
        )
        mark_payout_processing(
            tenant=self.tenant,
            payout=second_payout,
            admin_user=self.teacher,
        )

        with self.assertRaises(ValidationError):
            settle_payout(
                tenant=self.tenant,
                payout=second_payout,
                external_ref=txhash,
                admin_user=self.teacher,
            )

    def test_release_payout_bounds_ledger_description_within_255_chars(self):
        long_reason = "R" * 300
        entry = release_payout(
            tenant=self.tenant,
            payout=self.payout,
            reason=long_reason,
            admin_user=self.teacher,
        )
        self.assertLessEqual(len(entry.description), 255)
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.REJECTED)
