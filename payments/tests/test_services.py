from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from decimal import Decimal
import threading

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from accounts.models import Tenant, User
from payments.models import Order, Payment, PaymentAddress
from payments.services import (
    create_topup_order,
    mark_payment_successful,
    process_manual_topup,
)
from wallet.models import LedgerEntry, Wallet


class CreateTopupOrderTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_order_test",
            password="testpassword",
            role=User.Role.TEACHER,
            first_name="Order",
            last_name="Teacher",
        )
        self.teacher_profile = self.teacher_user.teacher_profile
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher_profile)

        self.student_user = User.objects.create_user(
            username="student_order_test",
            password="testpassword",
            role=User.Role.STUDENT,
            first_name="Order",
            last_name="Student",
        )

        # Provision active PaymentAddress
        self.active_address = PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address="0x1234567890123456789012345678901234567890",
            status=PaymentAddress.Status.ACTIVE,
        )

    def test_create_topup_order_success(self):
        order = create_topup_order(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
            currency="USD",
        )
        self.assertIsNotNone(order.pk)
        self.assertEqual(order.tenant, self.tenant)
        self.assertEqual(order.student, self.student_user)
        self.assertEqual(order.amount, Decimal("25.00"))
        self.assertEqual(order.currency, "USD")
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertTrue(order.idempotency_key)
        self.assertGreater(order.expires_at, timezone.now())

        # Check snapshot
        self.assertEqual(
            order.payment_instructions_snapshot.get("provider"),
            "crypto_usdt",
        )
        self.assertEqual(
            order.payment_instructions_snapshot.get("address"),
            self.active_address.address,
        )
        self.assertEqual(
            order.payment_instructions_snapshot.get("receiving_address"),
            self.active_address.address,
        )

    def test_create_topup_order_duplicate_prevention(self):
        order1 = create_topup_order(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("30.00"),
        )
        order2 = create_topup_order(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("30.00"),
        )
        self.assertEqual(order1.pk, order2.pk)
        self.assertEqual(
            Order.objects.filter(tenant=self.tenant, student=self.student_user).count(),
            1,
        )

    def test_create_topup_order_below_min_amount_raises_validation_error(self):
        self.tenant.min_topup_amount = Decimal("20.00")
        self.tenant.save(update_fields=["min_topup_amount"])

        with self.assertRaises(ValidationError):
            create_topup_order(
                tenant=self.tenant,
                student=self.student_user,
                amount=Decimal("19.99"),
            )

    def test_create_topup_order_invalid_amount_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            create_topup_order(
                tenant=self.tenant,
                student=self.student_user,
                amount=Decimal("0.00"),
            )
        with self.assertRaises(ValidationError):
            create_topup_order(
                tenant=self.tenant,
                student=self.student_user,
                amount=Decimal("-5.00"),
            )

    def test_create_topup_order_no_active_payment_address_raises_validation_error(self):
        self.active_address.status = PaymentAddress.Status.RETIRED
        self.active_address.save(update_fields=["status"])

        with self.assertRaises(ValidationError) as ctx:
            create_topup_order(
                tenant=self.tenant,
                student=self.student_user,
                amount=Decimal("25.00"),
            )
        self.assertIn("No active payment address", str(ctx.exception))

    def test_create_topup_order_creates_new_if_previous_expired(self):
        # Create an expired order
        Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key="expired_order_key",
            expires_at=timezone.now() - timedelta(hours=1),
        )

        new_order = create_topup_order(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
        )
        self.assertNotEqual(new_order.idempotency_key, "expired_order_key")
        self.assertGreater(new_order.expires_at, timezone.now())
        self.assertEqual(
            Order.objects.filter(tenant=self.tenant, student=self.student_user).count(),
            2,
        )


class MarkPaymentSuccessfulTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_mark_test",
            password="testpassword",
            role=User.Role.TEACHER,
            first_name="Mark",
            last_name="Teacher",
        )
        self.teacher_profile = self.teacher_user.teacher_profile
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher_profile)

        self.student_user = User.objects.create_user(
            username="student_mark_test",
            password="testpassword",
            role=User.Role.STUDENT,
            first_name="Mark",
            last_name="Student",
        )

        self.payment = Payment.objects.create(
            tenant=self.tenant,
            claimed_by=self.student_user,
            provider="manual",
            external_id=f"tenant_{self.tenant.id}:manual_ref_101",
            gross_amount=Decimal("25.00"),
            fee_amount=Decimal("0.00"),
            net_amount=Decimal("25.00"),
            currency="USD",
            status=Payment.Status.INITIATED,
        )

    def test_mark_payment_successful_credits_wallet_and_completes_payment(self):
        updated_payment = mark_payment_successful(payment=self.payment)
        self.assertEqual(updated_payment.status, Payment.Status.COMPLETED)
        self.assertIsNotNone(updated_payment.completed_at)

        wallet = Wallet.objects.get(
            tenant=self.tenant,
            user=self.student_user,
            currency="USD",
        )
        self.assertEqual(wallet.settled_balance, Decimal("25.00"))
        self.assertEqual(wallet.available_balance, Decimal("25.00"))

        entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:payment:{self.payment.id}:credit"
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.CREDIT)
        self.assertEqual(entry.amount, Decimal("25.00"))
        self.assertEqual(entry.settled_after, Decimal("25.00"))

    def test_mark_payment_successful_with_linked_order(self):
        order = Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key="order_linked_key",
            expires_at=timezone.now() + timedelta(hours=24),
        )
        self.payment.order = order
        self.payment.save(update_fields=["order"])

        mark_payment_successful(payment=self.payment)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertIsNotNone(order.completed_at)

    def test_mark_payment_successful_updates_raw_metadata(self):
        mark_payment_successful(
            payment=self.payment,
            raw_metadata={"txhash": "0xabc123", "verified": True},
        )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.raw_metadata.get("txhash"), "0xabc123")
        self.assertTrue(self.payment.raw_metadata.get("verified"))

    def test_mark_payment_successful_idempotent_if_already_completed(self):
        mark_payment_successful(payment=self.payment)
        wallet = Wallet.objects.get(
            tenant=self.tenant,
            user=self.student_user,
            currency="USD",
        )
        self.assertEqual(wallet.settled_balance, Decimal("25.00"))

        # Second call does not credit again
        mark_payment_successful(payment=self.payment)
        wallet.refresh_from_db()
        self.assertEqual(wallet.settled_balance, Decimal("25.00"))
        self.assertEqual(
            LedgerEntry.objects.filter(
                idempotency_key=f"tenant:{self.tenant.id}:payment:{self.payment.id}:credit"
            ).count(),
            1,
        )

    def test_mark_payment_successful_tenant_mismatch_raises_validation_error(self):
        other_teacher = User.objects.create_user(
            username="teacher_other_tenant",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        other_tenant = Tenant.objects.get(teacher_profile=other_teacher.teacher_profile)
        order_other_tenant = Order.objects.create(
            tenant=other_tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key="order_other_tenant_key",
            expires_at=timezone.now() + timedelta(hours=24),
        )
        self.payment.order = order_other_tenant
        self.payment.save(update_fields=["order"])

        with self.assertRaises(ValidationError) as ctx:
            mark_payment_successful(payment=self.payment)
        self.assertIn("tenant mismatch", str(ctx.exception))



class ProcessManualTopupTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_manual_test",
            password="testpassword",
            role=User.Role.TEACHER,
            first_name="Manual",
            last_name="Teacher",
        )
        self.teacher_profile = self.teacher_user.teacher_profile
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher_profile)

        self.student_user = User.objects.create_user(
            username="student_manual_test",
            password="testpassword",
            role=User.Role.STUDENT,
            first_name="Manual",
            last_name="Student",
        )

        self.admin_user = User.objects.create_superuser(
            username="admin_manual_test",
            password="testpassword",
            first_name="Admin",
            last_name="User",
        )

    def test_process_manual_topup_success(self):
        payment = process_manual_topup(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("50.00"),
            reference_id="receipt_tx_12345",
            admin_user=self.admin_user,
            notes="Offline bank transfer verified",
        )

        self.assertIsNotNone(payment.pk)
        self.assertEqual(payment.tenant, self.tenant)
        self.assertEqual(payment.claimed_by, self.student_user)
        self.assertEqual(payment.provider, "manual")
        self.assertEqual(
            payment.external_id,
            f"tenant_{self.tenant.id}:manual_receipt_tx_12345",
        )
        self.assertEqual(payment.gross_amount, Decimal("50.00"))
        self.assertEqual(payment.fee_amount, Decimal("0.00"))
        self.assertEqual(payment.net_amount, Decimal("50.00"))
        self.assertEqual(payment.status, Payment.Status.COMPLETED)
        self.assertIsNotNone(payment.completed_at)
        self.assertEqual(payment.raw_metadata.get("notes"), "Offline bank transfer verified")
        self.assertEqual(payment.raw_metadata.get("admin_user_id"), self.admin_user.id)

        wallet = Wallet.objects.get(
            tenant=self.tenant,
            user=self.student_user,
            currency="USD",
        )
        self.assertEqual(wallet.settled_balance, Decimal("50.00"))
        self.assertEqual(wallet.available_balance, Decimal("50.00"))

        entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:payment:{payment.id}:credit"
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.CREDIT)
        self.assertEqual(entry.amount, Decimal("50.00"))
        self.assertEqual(entry.settled_after, Decimal("50.00"))

    def test_process_manual_topup_links_matching_pending_order(self):
        order = Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("40.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key="order_matching_key",
            expires_at=timezone.now() + timedelta(hours=24),
        )

        payment = process_manual_topup(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("40.00"),
            reference_id="receipt_order_40",
            admin_user=self.admin_user,
        )

        self.assertEqual(payment.order, order)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertIsNotNone(order.completed_at)

    def test_process_manual_topup_does_not_link_mismatched_amount_order(self):
        order = Order.objects.create(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("100.00"),
            currency="USD",
            status=Order.Status.PENDING,
            idempotency_key="order_100_key",
            expires_at=timezone.now() + timedelta(hours=24),
        )

        payment = process_manual_topup(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("25.00"),
            reference_id="receipt_order_25",
            admin_user=self.admin_user,
        )

        self.assertIsNone(payment.order)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertIsNone(order.completed_at)


    def test_process_manual_topup_below_min_amount_raises_validation_error(self):
        self.tenant.min_topup_amount = Decimal("20.00")
        self.tenant.save(update_fields=["min_topup_amount"])

        with self.assertRaises(ValidationError):
            process_manual_topup(
                tenant=self.tenant,
                student=self.student_user,
                amount=Decimal("15.00"),
                reference_id="receipt_low",
                admin_user=self.admin_user,
            )

        # Confirm no payment or wallet created
        self.assertFalse(Payment.objects.filter(tenant=self.tenant).exists())
        self.assertFalse(Wallet.objects.filter(tenant=self.tenant).exists())

    def test_process_manual_topup_empty_reference_id_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            process_manual_topup(
                tenant=self.tenant,
                student=self.student_user,
                amount=Decimal("25.00"),
                reference_id="   ",
                admin_user=self.admin_user,
            )

    def test_process_manual_topup_idempotent_resolution_when_completed(self):
        payment1 = process_manual_topup(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("35.00"),
            reference_id="receipt_idempotent_test",
            admin_user=self.admin_user,
        )

        payment2 = process_manual_topup(
            tenant=self.tenant,
            student=self.student_user,
            amount=Decimal("35.00"),
            reference_id="receipt_idempotent_test",
            admin_user=self.admin_user,
        )

        self.assertEqual(payment1.pk, payment2.pk)
        wallet = Wallet.objects.get(tenant=self.tenant, user=self.student_user)
        self.assertEqual(wallet.settled_balance, Decimal("35.00"))
        self.assertEqual(
            LedgerEntry.objects.filter(
                idempotency_key=f"tenant:{self.tenant.id}:payment:{payment1.id}:credit"
            ).count(),
            1,
        )


class ManualTopupConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_concurrency_test",
            password="testpassword",
            role=User.Role.TEACHER,
            first_name="Concurrent",
            last_name="Teacher",
        )
        self.teacher_profile = self.teacher_user.teacher_profile
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher_profile)

        self.student_user = User.objects.create_user(
            username="student_concurrency_test",
            password="testpassword",
            role=User.Role.STUDENT,
            first_name="Concurrent",
            last_name="Student",
        )

        self.admin_user = User.objects.create_superuser(
            username="admin_concurrency_test",
            password="testpassword",
            first_name="Admin",
            last_name="Concurrent",
        )

    def test_parallel_duplicate_topup_race(self):
        """
        Parallel duplicate top-up race test: 5 concurrent threads invoke process_manual_topup
        with identical reference_id. Exactly 1 transaction commits successfully, 4 abort with
        IntegrityError, and student wallet settled balance increases by exactly 1 top-up amount.
        """
        num_threads = 5
        reference_id = "concurrent_race_ref_001"
        amount = Decimal("30.00")
        barrier = threading.Barrier(num_threads)

        results = []
        exceptions = []
        lock = threading.Lock()

        def _worker(thread_idx):
            try:
                barrier.wait(timeout=10.0)
                payment = process_manual_topup(
                    tenant=self.tenant,
                    student=self.student_user,
                    amount=amount,
                    reference_id=reference_id,
                    admin_user=self.admin_user,
                    notes=f"Concurrent thread {thread_idx}",
                )
                with lock:
                    results.append((thread_idx, payment))
            except Exception as exc:
                with lock:
                    exceptions.append((thread_idx, exc))
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(_worker, i) for i in range(num_threads)]
            for future in as_completed(futures):
                future.result()

        # Exactly 1 thread must succeed
        self.assertEqual(len(results), 1, f"Expected 1 success, got {len(results)}")
        # Exactly 4 threads must fail
        self.assertEqual(len(exceptions), 4, f"Expected 4 failures, got {len(exceptions)}")

        # All 4 failures must be IntegrityError
        for thread_idx, exc in exceptions:
            self.assertIsInstance(
                exc,
                IntegrityError,
                f"Thread {thread_idx} raised {type(exc).__name__}: {exc}",
            )

        # Student wallet settled balance must increase by exactly 1 top-up amount ($30.00)
        wallet = Wallet.objects.get(
            tenant=self.tenant,
            user=self.student_user,
            currency="USD",
        )
        self.assertEqual(wallet.settled_balance, amount)
        self.assertEqual(wallet.available_balance, amount)

        # Exactly 1 credit LedgerEntry must be created
        self.assertEqual(
            LedgerEntry.objects.filter(
                tenant=self.tenant,
                wallet=wallet,
                entry_type=LedgerEntry.EntryType.CREDIT,
            ).count(),
            1,
        )

        # Exactly 1 Payment record must exist in the database
        self.assertEqual(
            Payment.objects.filter(
                tenant=self.tenant,
                external_id=f"tenant_{self.tenant.id}:manual_{reference_id}",
            ).count(),
            1,
        )



