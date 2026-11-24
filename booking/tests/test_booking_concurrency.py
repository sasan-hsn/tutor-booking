from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Tenant, User
from booking.models import Booking, RegularAvailability
from booking.services import create_booking_with_hold
from tutor_booking.tests.concurrency import run_concurrently
from wallet.exceptions import InsufficientBalanceError
from wallet.models import LedgerEntry, Wallet
from wallet.services import (
    compute_expected_balances,
    credit_wallet,
    get_or_create_wallet,
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
