from datetime import time, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Tenant, User
from booking.models import Booking, RegularAvailability
from booking.services import create_booking_with_hold, expire_stale_bookings
from booking.tasks import expire_pending_bookings_task
from wallet.models import LedgerEntry
from wallet.services import credit_wallet, get_or_create_wallet


class BookingViewSettlementTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_view_settle",
            email="teacher_view_settle@example.com",
            password="password123",
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

        self.student_user = User.objects.create_user(
            username="student_view_settle",
            email="student_view_settle@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="view-settle-seed",
            reference_id="view_seed",
            description="Seed $100.00",
        )

        self.teacher_client = self.client_class()
        self.teacher_client.force_login(self.teacher_user)

        self.tz = ZoneInfo(self.teacher_user.timezone)
        future_day = timezone.localdate() + timedelta(days=3)
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=future_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        self.future_slot = timezone.datetime.combine(
            future_day, time(10, 0), tzinfo=self.tz
        )

    def _create_past_reserved_booking(self, hours_ago=4):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=self.future_slot,
            lesson_option="regular_50",
        )
        past_start = timezone.now() - timedelta(hours=hours_ago)
        booking.status = Booking.Status.CONFIRMED
        booking.start_at = past_start
        booking.end_at = past_start + timedelta(minutes=50)
        booking.save()
        return booking

    def test_complete_lesson_settles_consume_completed(self):
        booking = self._create_past_reserved_booking()

        response = self.teacher_client.post(
            reverse("booking:complete_lesson", kwargs={"booking_id": booking.id}),
            {"note": "Great work"},
        )

        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.COMPLETED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.CONSUMED)
        self.assertEqual(booking.settlement_reason, Booking.SettlementReason.COMPLETED)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("75.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

        entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.CONSUME)
        self.assertEqual(entry.amount, Decimal("25.00"))

    def test_mark_lesson_not_held_teacher_fault_releases_hold(self):
        booking = self._create_past_reserved_booking()

        response = self.teacher_client.post(
            reverse("booking:mark_lesson_not_held", kwargs={"booking_id": booking.id}),
            {"fault_party": "teacher"},
        )

        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            booking.settlement_reason, Booking.SettlementReason.NOT_HELD_TEACHER_FAULT
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

        entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.RELEASE)

    def test_mark_lesson_not_held_student_fault_consumes_hold(self):
        booking = self._create_past_reserved_booking()

        response = self.teacher_client.post(
            reverse("booking:mark_lesson_not_held", kwargs={"booking_id": booking.id}),
            {"fault_party": "student"},
        )

        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.CONSUMED)
        self.assertEqual(
            booking.settlement_reason, Booking.SettlementReason.STUDENT_ABSENT
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("75.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

        entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.CONSUME)

    def test_mark_lesson_not_held_reserved_requires_valid_fault_party(self):
        booking = self._create_past_reserved_booking()
        url = reverse("booking:mark_lesson_not_held", kwargs={"booking_id": booking.id})

        for payload in [{}, {"fault_party": "platform"}]:
            response = self.teacher_client.post(url, payload)
            self.assertEqual(response.status_code, 400)
            booking.refresh_from_db()
            self.assertEqual(booking.status, Booking.Status.CONFIRMED)
            self.assertEqual(booking.payment_status, Booking.PaymentStatus.RESERVED)
            self.wallet.refresh_from_db()
            self.assertEqual(self.wallet.reserved_balance, Decimal("25.00"))

    def test_mark_lesson_not_held_unpaid_allows_omitted_fault_party(self):
        self.tenant.payments_enabled = False
        self.tenant.save()
        past_start = timezone.now() - timedelta(hours=4)
        booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=past_start,
            end_at=past_start + timedelta(minutes=50),
            status=Booking.Status.CONFIRMED,
            payment_status=Booking.PaymentStatus.UNPAID,
        )

        response = self.teacher_client.post(
            reverse("booking:mark_lesson_not_held", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.UNPAID)
        self.assertIsNone(booking.settlement_reason)
        self.assertEqual(
            LedgerEntry.objects.filter(
                entry_type=LedgerEntry.EntryType.RELEASE
            ).count(),
            0,
        )

    def test_cancel_lesson_by_teacher_releases_confirmed_and_pending_holds(self):
        confirmed_booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=self.future_slot,
            lesson_option="regular_50",
        )
        confirmed_booking.status = Booking.Status.CONFIRMED
        confirmed_booking.save()

        resp1 = self.teacher_client.post(
            reverse(
                "booking:cancel_lesson", kwargs={"booking_id": confirmed_booking.id}
            )
        )
        self.assertEqual(resp1.status_code, 200)
        confirmed_booking.refresh_from_db()
        self.assertEqual(confirmed_booking.status, Booking.Status.CANCELLED)
        self.assertEqual(
            confirmed_booking.payment_status, Booking.PaymentStatus.RELEASED
        )
        self.assertEqual(
            confirmed_booking.settlement_reason,
            Booking.SettlementReason.TEACHER_CANCELLED,
        )

        pending_booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=self.future_slot + timedelta(hours=2),
            lesson_option="regular_50",
        )
        resp2 = self.teacher_client.post(
            reverse("booking:cancel_lesson", kwargs={"booking_id": pending_booking.id})
        )
        self.assertEqual(resp2.status_code, 200)
        pending_booking.refresh_from_db()
        self.assertEqual(pending_booking.status, Booking.Status.CANCELLED)
        self.assertEqual(pending_booking.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            pending_booking.settlement_reason,
            Booking.SettlementReason.TEACHER_CANCELLED,
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_respond_to_booking_decline_releases_hold_with_teacher_declined(self):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=self.future_slot,
            lesson_option="regular_50",
        )

        response = self.teacher_client.post(
            reverse("booking:respond_to_booking", kwargs={"booking_id": booking.id}),
            {"action": "decline"},
        )
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            booking.settlement_reason, Booking.SettlementReason.TEACHER_DECLINED
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_respond_to_booking_accept_cancellation_request_releases_hold(self):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=self.future_slot,
            lesson_option="regular_50",
        )
        booking.status = Booking.Status.CONFIRMED
        booking.cancellation_requested = True
        booking.save()

        response = self.teacher_client.post(
            reverse("booking:respond_to_booking", kwargs={"booking_id": booking.id}),
            {"action": "accept"},
        )
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertFalse(booking.cancellation_requested)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            booking.settlement_reason, Booking.SettlementReason.STUDENT_CANCELLED
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))


class BookingExpirationSettlementTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_exp_settle",
            email="teacher_exp_settle@example.com",
            password="password123",
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

        self.student_user = User.objects.create_user(
            username="student_exp_settle",
            email="student_exp_settle@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="exp-settle-seed",
            reference_id="exp_seed",
            description="Seed $100.00",
        )

        self.tz = ZoneInfo(self.teacher_user.timezone)
        future_day = timezone.localdate() + timedelta(days=3)
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=future_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        self.slot_1 = timezone.datetime.combine(future_day, time(10, 0), tzinfo=self.tz)
        self.slot_2 = timezone.datetime.combine(future_day, time(12, 0), tzinfo=self.tz)

    def _make_stale_pending_reserved(self, slot, hours_ago=2):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=slot,
            lesson_option="regular_50",
        )
        past_start = timezone.now() - timedelta(hours=hours_ago)
        booking.start_at = past_start
        booking.end_at = past_start + timedelta(minutes=50)
        booking.save()
        return booking

    def test_expire_stale_bookings_releases_hold_with_expired_pending(self):
        booking = self._make_stale_pending_reserved(self.slot_1)

        expired_count = expire_stale_bookings()
        self.assertEqual(expired_count, 1)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.EXPIRED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            booking.settlement_reason, Booking.SettlementReason.EXPIRED_PENDING
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

        entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.RELEASE)

    def test_expire_pending_bookings_task_releases_hold(self):
        booking = self._make_stale_pending_reserved(self.slot_1)

        expired_count = expire_pending_bookings_task()
        self.assertEqual(expired_count, 1)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.EXPIRED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            booking.settlement_reason, Booking.SettlementReason.EXPIRED_PENDING
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_respond_to_stale_pending_booking_expires_and_releases_hold(self):
        booking = self._make_stale_pending_reserved(self.slot_1)
        teacher_client = self.client_class()
        teacher_client.force_login(self.teacher_user)

        response = teacher_client.post(
            reverse("booking:respond_to_booking", kwargs={"booking_id": booking.id}),
            {"action": "accept"},
        )
        self.assertEqual(response.status_code, 400)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.EXPIRED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            booking.settlement_reason, Booking.SettlementReason.EXPIRED_PENDING
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_expire_stale_bookings_commits_each_booking_independently(self):
        b1 = self._make_stale_pending_reserved(self.slot_1, hours_ago=3)
        b2 = self._make_stale_pending_reserved(self.slot_2, hours_ago=2)

        from wallet.services import settle_booking as real_settle_booking

        def fail_on_second(*, tenant, booking, action, actor=None, reason):
            if booking.id == b2.id:
                raise RuntimeError("Simulated failure on second booking")
            return real_settle_booking(
                tenant=tenant,
                booking=booking,
                action=action,
                actor=actor,
                reason=reason,
            )

        with patch("booking.services.settle_booking", side_effect=fail_on_second):
            with self.assertRaises(RuntimeError):
                expire_stale_bookings()

        b1.refresh_from_db()
        b2.refresh_from_db()
        self.assertEqual(b1.status, Booking.Status.EXPIRED)
        self.assertEqual(b1.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(b2.status, Booking.Status.PENDING)
        self.assertEqual(b2.payment_status, Booking.PaymentStatus.RESERVED)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("25.00"))
