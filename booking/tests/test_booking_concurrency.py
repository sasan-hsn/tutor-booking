from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Tenant, TermsAcceptance, User
from booking.models import Booking, RegularAvailability
from booking.services import (
    DisputeConflictError,
    create_booking_with_hold,
    open_dispute,
    resolve_dispute,
)
from booking.tasks import auto_complete_lessons_task
from tutor_booking.tests.concurrency import run_concurrently
from wallet.exceptions import InsufficientBalanceError
from wallet.models import LedgerEntry, Wallet
from wallet.services import (
    compute_expected_balances,
    credit_wallet,
    get_or_create_wallet,
    settle_booking,
)


class DoubleBookingEndpointConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="testteacher",
            password="test123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher = self.teacher_user.teacher_profile

        self.student1 = User.objects.create_user(
            username="student1",
            password="test123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.student2 = User.objects.create_user(
            username="student2",
            password="test123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

        # Ensure slot is on a future Monday
        today = timezone.now().date()
        days_until_next_monday = (0 - today.weekday()) % 7
        if days_until_next_monday <= 0:
            days_until_next_monday += 7
        next_monday = today + timedelta(days=days_until_next_monday)
        self.teacher_tz = ZoneInfo(self.teacher_user.timezone)
        self.slot_start_at = datetime.combine(
            next_monday, time(10, 0), tzinfo=self.teacher_tz
        )

        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(10, 0),
            end_time=time(11, 0),
        )

        self.book_url = reverse("booking:book_slot")

    def test_concurrent_requests_to_endpoint_only_one_succeeds(self):
        """Two real HTTP POST requests hitting book_slot at the same time — only one should succeed."""

        def make_request(username):
            client = Client()
            client.login(username=username, password="test123")
            response = client.post(
                self.book_url,
                {
                    "start_at": self.slot_start_at.isoformat(),
                    "lesson_option": "trial",
                },
            )
            return response.status_code

        results = run_concurrently(
            [
                lambda: make_request("student1"),
                lambda: make_request("student2"),
            ]
        )
        status_codes = []
        for res, exc in results:
            if exc is not None:
                raise exc
            status_codes.append(res)

        self.assertIn(200, status_codes)
        self.assertIn(409, status_codes)
        self.assertEqual(
            Booking.objects.filter(
                teacher=self.teacher,
                start_at=self.slot_start_at,
                status__in=[Booking.Status.PENDING, Booking.Status.CONFIRMED],
            ).count(),
            1,
        )


class BookingHoldConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_hold_conc",
            email="teacher_hold_conc@example.com",
            password="test123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.lesson_price = Decimal("25.00")
        self.teacher.lesson_price_25 = Decimal("15.00")
        self.teacher.offers_trial = False
        self.teacher.save()

        self.tenant = Tenant.objects.get(teacher_profile=self.teacher)
        self.tenant.payments_enabled = True
        self.tenant.save()

        self.student = User.objects.create_user(
            username="student_hold_conc",
            email="student_hold_conc@example.com",
            password="test123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        TermsAcceptance.objects.create(
            user=self.student,
            tenant=self.tenant,
            version=settings.TERMS_CURRENT_VERSION,
            ip_address="127.0.0.1",
            user_agent="TestSuite",
        )

        today = timezone.now().date()
        days_until_next_monday = (0 - today.weekday()) % 7
        if days_until_next_monday <= 0:
            days_until_next_monday += 7
        next_monday = today + timedelta(days=days_until_next_monday)
        self.teacher_tz = ZoneInfo(self.teacher_user.timezone)
        self.slot_starts = [
            datetime.combine(next_monday, time(hour, 0), tzinfo=self.teacher_tz)
            for hour in range(9, 14)
        ]

        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(9, 0),
            end_time=time(17, 0),
        )

    def test_first_booking_concurrent_wallet_creation_creates_single_wallet(self):
        """2 concurrent wallet provisioning calls inside transactions create 1 Wallet without IntegrityError."""

        def provision():
            with transaction.atomic():
                return get_or_create_wallet(tenant=self.tenant, user=self.student).pk

        results = run_concurrently([provision, provision])
        wallet_ids = []
        for res, exc in results:
            self.assertIsNone(
                exc, f"Unexpected exception during wallet creation: {exc}"
            )
            wallet_ids.append(res)

        self.assertEqual(len(set(wallet_ids)), 1)
        self.assertEqual(
            Wallet.objects.filter(tenant=self.tenant, user=self.student).count(),
            1,
        )

    def test_first_booking_concurrent_http_requests_for_new_student_no_500(self):
        """2 concurrent initial HTTP booking requests for a student without a wallet fail cleanly with 402."""
        book_url = reverse("booking:book_slot")

        def make_request(slot_start):
            client = Client()
            client.login(username="student_hold_conc", password="test123")
            return client.post(
                book_url,
                {
                    "start_at": slot_start.isoformat(),
                    "lesson_option": "regular_50",
                },
            ).status_code

        results = run_concurrently(
            [
                lambda: make_request(self.slot_starts[0]),
                lambda: make_request(self.slot_starts[1]),
            ]
        )
        statuses = []
        for res, exc in results:
            self.assertIsNone(exc)
            statuses.append(res)

        self.assertEqual(statuses, [402, 402])
        self.assertEqual(Booking.objects.count(), 0)

    def test_parallel_booking_double_spend_race_allows_only_one_reservation(self):
        """
        5 concurrent threads attempt to reserve $25.00 simultaneously with $25.00 available.
        Exactly 1 succeeds, 4 fail with InsufficientBalanceError, and final balances are
        settled $25.00, reserved $25.00, available $0.00.
        """
        wallet = get_or_create_wallet(tenant=self.tenant, user=self.student)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("25.00"),
            idempotency_key="race-seed-25",
            reference_id="race_seed",
            description="Seed $25.00 for double-spend race",
        )

        def attempt_booking(slot_start):
            return create_booking_with_hold(
                teacher=self.teacher,
                student=self.student,
                start_at=slot_start,
                lesson_option="regular_50",
            )

        targets = [(lambda s=slot: attempt_booking(s)) for slot in self.slot_starts]
        results = run_concurrently(targets)

        successes = [res for res, exc in results if exc is None]
        exceptions = [exc for res, exc in results if exc is not None]

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(exceptions), 4)
        for exc in exceptions:
            self.assertIsInstance(exc, InsufficientBalanceError)

        self.assertEqual(Booking.objects.count(), 1)
        booking = Booking.objects.first()
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RESERVED)
        self.assertEqual(booking.held_amount, Decimal("25.00"))

        wallet.refresh_from_db()
        self.assertEqual(wallet.settled_balance, Decimal("25.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("25.00"))
        self.assertEqual(wallet.available_balance, Decimal("0.00"))

        self.assertEqual(
            LedgerEntry.objects.filter(
                wallet=wallet,
                entry_type=LedgerEntry.EntryType.RESERVE,
            ).count(),
            1,
        )
        expected = compute_expected_balances(wallet)
        self.assertEqual(wallet.settled_balance, expected.expected_settled)
        self.assertEqual(wallet.reserved_balance, expected.expected_reserved)

    def test_parallel_terminal_settlement_race_commits_exactly_one_entry(self):
        """
        Simultaneous consume (auto-complete) vs release (cancellation) on real PostgreSQL
        commits exactly 1 terminal settlement entry and short-circuits the second call cleanly.
        """
        wallet = get_or_create_wallet(tenant=self.tenant, user=self.student)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("50.00"),
            idempotency_key="race-settle-seed-50",
            reference_id="race_settle_seed",
            description="Seed $50.00 for terminal settlement race",
        )
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student,
            start_at=self.slot_starts[0],
            lesson_option="regular_50",
        )

        def run_consume():
            b = Booking.objects.get(pk=booking.pk)
            return settle_booking(
                tenant=self.tenant,
                booking=b,
                action="consume",
                actor=self.teacher_user,
                reason=Booking.SettlementReason.COMPLETED,
            )

        def run_release():
            b = Booking.objects.get(pk=booking.pk)
            return settle_booking(
                tenant=self.tenant,
                booking=b,
                action="release",
                actor=self.teacher_user,
                reason=Booking.SettlementReason.TEACHER_CANCELLED,
            )

        results = run_concurrently([run_consume, run_release])
        for _, exc in results:
            self.assertIsNone(
                exc, f"Unexpected exception during settlement race: {exc}"
            )

        entries = [res for res, _ in results if res is not None]
        nones = [res for res, _ in results if res is None]
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(nones), 1)

        settle_key = f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
        db_entries = LedgerEntry.objects.filter(idempotency_key=settle_key)
        self.assertEqual(db_entries.count(), 1)
        winner = db_entries.first()

        booking.refresh_from_db()
        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))

        if winner.entry_type == LedgerEntry.EntryType.CONSUME:
            self.assertEqual(booking.payment_status, Booking.PaymentStatus.CONSUMED)
            self.assertEqual(
                booking.settlement_reason, Booking.SettlementReason.COMPLETED
            )
            self.assertEqual(wallet.settled_balance, Decimal("25.00"))
        else:
            self.assertEqual(winner.entry_type, LedgerEntry.EntryType.RELEASE)
            self.assertEqual(booking.payment_status, Booking.PaymentStatus.RELEASED)
            self.assertEqual(
                booking.settlement_reason, Booking.SettlementReason.TEACHER_CANCELLED
            )
            self.assertEqual(wallet.settled_balance, Decimal("50.00"))

        expected = compute_expected_balances(wallet)
        self.assertEqual(wallet.settled_balance, expected.expected_settled)
        self.assertEqual(wallet.reserved_balance, expected.expected_reserved)

    def test_concurrent_hold_and_settlement_no_deadlock(self):
        """
        Concurrent create_booking_with_hold and settle_booking on the same TeacherProfile and Wallet
        complete cleanly without deadlock due to unified TeacherProfile -> Booking -> Wallet locking.
        """
        wallet = get_or_create_wallet(tenant=self.tenant, user=self.student)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("100.00"),
            idempotency_key="race-lock-order-seed",
            reference_id="race_lock_seed",
            description="Seed $100.00 for lock hierarchy test",
        )
        existing_booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student,
            start_at=self.slot_starts[0],
            lesson_option="regular_50",
        )

        def create_second():
            return create_booking_with_hold(
                teacher=self.teacher,
                student=self.student,
                start_at=self.slot_starts[1],
                lesson_option="regular_50",
            )

        def settle_first():
            b = Booking.objects.get(pk=existing_booking.pk)
            return settle_booking(
                tenant=self.tenant,
                booking=b,
                action="consume",
                actor=self.teacher_user,
                reason=Booking.SettlementReason.COMPLETED,
            )

        results = run_concurrently([create_second, settle_first])
        for res, exc in results:
            self.assertIsNone(exc, f"Unexpected deadlock or exception: {exc}")
            self.assertIsNotNone(res)

        wallet.refresh_from_db()
        self.assertEqual(wallet.settled_balance, Decimal("75.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("25.00"))
        expected = compute_expected_balances(wallet)
        self.assertEqual(wallet.settled_balance, expected.expected_settled)
        self.assertEqual(wallet.reserved_balance, expected.expected_reserved)

    def test_dispute_vs_auto_complete_race_at_48h_boundary(self):
        """
        Thread A opens a dispute while Thread B runs auto_complete_lessons_task at the
        exact 48h boundary under TeacherProfile -> Booking -> Wallet row locks.
        Exactly one state transition wins deterministically.
        """
        wallet = get_or_create_wallet(tenant=self.tenant, user=self.student)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("50.00"),
            idempotency_key="race-dispute-48h-seed",
            reference_id="race_dispute_seed",
            description="Seed $50.00 for dispute vs auto-complete race",
        )
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student,
            start_at=self.slot_starts[0],
            lesson_option="regular_50",
        )

        target_now = timezone.now()
        exact_end_at = target_now - timedelta(hours=settings.AUTO_COMPLETE_HOURS)
        booking.status = Booking.Status.CONFIRMED
        booking.end_at = exact_end_at
        booking.start_at = exact_end_at - timedelta(minutes=50)
        booking.save()

        def run_dispute():
            b = Booking.objects.get(pk=booking.pk)
            return open_dispute(booking=b, student=self.student, now=target_now)

        def run_auto_complete():
            return auto_complete_lessons_task(now=target_now)

        results = run_concurrently([run_dispute, run_auto_complete])
        (dispute_res, dispute_exc), (sweep_res, sweep_exc) = results
        self.assertIsNone(
            sweep_exc,
            f"Unexpected exception in auto_complete_lessons_task: {sweep_exc}",
        )

        booking.refresh_from_db()
        wallet.refresh_from_db()
        settle_key = f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
        settle_entries = LedgerEntry.objects.filter(idempotency_key=settle_key)

        if booking.status == Booking.Status.DISPUTING:
            self.assertIsNone(dispute_exc)
            self.assertIsNotNone(dispute_res)
            self.assertEqual(sweep_res, 0)
            self.assertEqual(booking.payment_status, Booking.PaymentStatus.RESERVED)
            self.assertEqual(settle_entries.count(), 0)
            self.assertEqual(wallet.settled_balance, Decimal("50.00"))
            self.assertEqual(wallet.reserved_balance, Decimal("25.00"))
        else:
            self.assertEqual(booking.status, Booking.Status.COMPLETED)
            self.assertEqual(sweep_res, 1)
            self.assertIsInstance(dispute_exc, ValidationError)
            self.assertIn("Lesson completion is final", str(dispute_exc))
            self.assertEqual(booking.payment_status, Booking.PaymentStatus.CONSUMED)
            self.assertEqual(
                booking.settlement_reason, Booking.SettlementReason.COMPLETED
            )
            self.assertEqual(settle_entries.count(), 1)
            self.assertEqual(
                settle_entries.first().entry_type, LedgerEntry.EntryType.CONSUME
            )
            self.assertEqual(wallet.settled_balance, Decimal("25.00"))
            self.assertEqual(wallet.reserved_balance, Decimal("0.00"))

        expected = compute_expected_balances(wallet)
        self.assertEqual(wallet.settled_balance, expected.expected_settled)
        self.assertEqual(wallet.reserved_balance, expected.expected_reserved)

    def test_concurrent_dispute_resolution_race_executes_once(self):
        """
        Two concurrent threads attempt to resolve the same DISPUTING booking
        (one in student favor, one in teacher favor) under TeacherProfile -> Booking -> Wallet locks.
        Exactly 1 succeeds, the other fails with DisputeConflictError, and exactly 1
        terminal settlement entry is committed.
        """
        wallet = get_or_create_wallet(tenant=self.tenant, user=self.student)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("50.00"),
            idempotency_key="race-dispute-resolve-seed",
            reference_id="race_resolve_seed",
            description="Seed $50.00 for dispute resolution race",
        )
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student,
            start_at=self.slot_starts[0],
            lesson_option="regular_50",
        )

        end_at = timezone.now() - timedelta(hours=2)
        booking.status = Booking.Status.CONFIRMED
        booking.end_at = end_at
        booking.start_at = end_at - timedelta(minutes=50)
        booking.save()

        open_dispute(
            booking=booking,
            student=self.student,
            reason="Concurrent resolution race test",
            now=end_at + timedelta(minutes=30),
        )

        def resolve_student():
            b = Booking.objects.get(pk=booking.pk)
            return resolve_dispute(
                tenant=self.tenant,
                booking=b,
                teacher_user=self.teacher_user,
                resolution=Booking.DisputeResolution.STUDENT_FAVOR,
            )

        def resolve_teacher():
            b = Booking.objects.get(pk=booking.pk)
            return resolve_dispute(
                tenant=self.tenant,
                booking=b,
                teacher_user=self.teacher_user,
                resolution=Booking.DisputeResolution.TEACHER_FAVOR,
            )

        results = run_concurrently([resolve_student, resolve_teacher])
        successes = [res for res, exc in results if exc is None]
        exceptions = [exc for res, exc in results if exc is not None]

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(exceptions), 1)
        self.assertIsInstance(exceptions[0], DisputeConflictError)

        booking.refresh_from_db()
        wallet.refresh_from_db()
        settle_key = f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
        settle_entries = LedgerEntry.objects.filter(idempotency_key=settle_key)
        self.assertEqual(settle_entries.count(), 1)
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))

        if booking.dispute_resolution == Booking.DisputeResolution.STUDENT_FAVOR:
            self.assertEqual(booking.status, Booking.Status.RESOLVED)
            self.assertEqual(booking.payment_status, Booking.PaymentStatus.RELEASED)
            self.assertEqual(wallet.settled_balance, Decimal("50.00"))
        else:
            self.assertEqual(
                booking.dispute_resolution, Booking.DisputeResolution.TEACHER_FAVOR
            )
            self.assertEqual(booking.status, Booking.Status.COMPLETED)
            self.assertEqual(booking.payment_status, Booking.PaymentStatus.CONSUMED)
            self.assertEqual(wallet.settled_balance, Decimal("25.00"))

        expected = compute_expected_balances(wallet)
        self.assertEqual(wallet.settled_balance, expected.expected_settled)
        self.assertEqual(wallet.reserved_balance, expected.expected_reserved)
