import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

from django.db import connections
from django.test import TransactionTestCase

from accounts.models import Tenant, User
from payments.exceptions import PayoutConflictError
from payments.models import Payout
from payments.services import (
    cancel_payout_request,
    create_payout_request,
    mark_payout_processing,
    verify_and_reserve_payout,
)
from wallet.models import LedgerEntry
from wallet.services import (
    compute_expected_balances,
    credit_wallet,
    get_or_create_wallet,
)


class ConcurrentPayoutCancelVsProcessingTests(TransactionTestCase):
    """
    Multi-threaded PostgreSQL concurrency suite verifying that concurrent execution
    of student cancellation and teacher mark_payout_processing produces deterministic
    outcomes with no orphaned holds or deadlocks.
    """

    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_conc_test",
            password="testpassword",
            role=User.Role.TEACHER,
            email="teacher_conc@example.com",
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)

        self.student = User.objects.create_user(
            username="student_conc_test",
            password="testpassword",
            role=User.Role.STUDENT,
            email="student_conc@example.com",
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
            idempotency_key="setup-credit-conc-50",
            reference_id="ref-conc-50",
            description="Initial top-up for concurrency tests",
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

    def test_concurrent_cancel_vs_processing_race(self):
        """
        Thread A calls cancel_payout_request while Thread B calls mark_payout_processing.
        Exactly one operation must succeed, the other must fail with PayoutConflictError (HTTP 409).
        The final payout status and wallet balances must remain completely consistent with no orphaned holds.
        """
        num_threads = 2
        barrier = threading.Barrier(num_threads)

        cancel_results = []
        processing_results = []
        cancel_exceptions = []
        processing_exceptions = []
        lock = threading.Lock()

        def _student_cancel_worker():
            try:
                barrier.wait(timeout=10.0)
                # Fetch fresh payout instance to simulate independent worker thread
                payout = Payout.objects.get(pk=self.payout.pk)
                res = cancel_payout_request(
                    tenant=self.tenant,
                    payout=payout,
                    user=self.student,
                )
                with lock:
                    cancel_results.append(res)
            except Exception as exc:
                with lock:
                    cancel_exceptions.append(exc)
            finally:
                connections.close_all()

        def _teacher_processing_worker():
            try:
                barrier.wait(timeout=10.0)
                # Fetch fresh payout instance to simulate independent worker thread
                payout = Payout.objects.get(pk=self.payout.pk)
                res = mark_payout_processing(
                    tenant=self.tenant,
                    payout=payout,
                    admin_user=self.teacher,
                )
                with lock:
                    processing_results.append(res)
            except Exception as exc:
                with lock:
                    processing_exceptions.append(exc)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(_student_cancel_worker),
                executor.submit(_teacher_processing_worker),
            ]
            for future in as_completed(futures):
                future.result()

        total_successes = len(cancel_results) + len(processing_results)
        total_failures = len(cancel_exceptions) + len(processing_exceptions)

        # Exactly 1 succeeds, exactly 1 fails
        self.assertEqual(
            total_successes,
            1,
            f"Expected exactly 1 success, got {total_successes} (cancel={len(cancel_results)}, proc={len(processing_results)})",
        )
        self.assertEqual(
            total_failures,
            1,
            f"Expected exactly 1 failure, got {total_failures} (cancel_err={cancel_exceptions}, proc_err={processing_exceptions})",
        )

        all_exceptions = cancel_exceptions + processing_exceptions
        for exc in all_exceptions:
            self.assertIsInstance(
                exc,
                PayoutConflictError,
                f"Failure should be PayoutConflictError, got {type(exc).__name__}: {exc}",
            )
            self.assertEqual(exc.status_code, 409)

        # Validate database state determinism
        self.payout.refresh_from_db()
        self.wallet.refresh_from_db()

        if len(cancel_results) == 1:
            # Student cancellation won the race
            self.assertEqual(self.payout.status, Payout.Status.CANCELLED)
            self.assertEqual(self.wallet.settled_balance, Decimal("50.00"))
            self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
            self.assertEqual(self.wallet.available_balance, Decimal("50.00"))
            self.assertEqual(
                LedgerEntry.objects.filter(
                    wallet=self.wallet,
                    entry_type=LedgerEntry.EntryType.PAYOUT_RELEASE,
                ).count(),
                1,
            )
        else:
            # Teacher mark_processing won the race
            self.assertEqual(self.payout.status, Payout.Status.PROCESSING)
            self.assertEqual(self.wallet.settled_balance, Decimal("50.00"))
            self.assertEqual(self.wallet.reserved_balance, Decimal("25.00"))
            self.assertEqual(self.wallet.available_balance, Decimal("25.00"))
            self.assertEqual(
                LedgerEntry.objects.filter(
                    wallet=self.wallet,
                    entry_type=LedgerEntry.EntryType.PAYOUT_RELEASE,
                ).count(),
                0,
            )

        # Ledger entries must exactly match expected balances
        expected = compute_expected_balances(self.wallet)
        self.assertEqual(self.wallet.settled_balance, expected.expected_settled)
        self.assertEqual(self.wallet.reserved_balance, expected.expected_reserved)
