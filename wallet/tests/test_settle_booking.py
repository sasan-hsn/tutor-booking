from datetime import time, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from accounts.models import Tenant, User
from booking.models import Booking, RegularAvailability
from booking.services import create_booking_with_hold
from wallet.exceptions import TenantMismatchError
from wallet.models import LedgerEntry
from wallet.services import credit_wallet, get_or_create_wallet, settle_booking


class SettleBookingServiceTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_settle",
            email="teacher_settle@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.lesson_price = Decimal("20.00")
        self.teacher.lesson_price_25 = Decimal("12.00")
        self.teacher.offers_trial = False
        self.teacher.save()

        self.tenant = Tenant.objects.get(teacher_profile=self.teacher)
        self.tenant.payments_enabled = True
        self.tenant.save()

        self.student = User.objects.create_user(
            username="student_settle",
            email="student_settle@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student)
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="settle-seed-50",
            reference_id="seed_50",
            description="Seed $50.00",
        )

        self.tz = ZoneInfo(self.teacher_user.timezone)
        future_day = timezone.localdate() + timedelta(days=2)
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=future_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        self.slot_1 = timezone.datetime.combine(future_day, time(10, 0), tzinfo=self.tz)
        self.slot_2 = timezone.datetime.combine(future_day, time(12, 0), tzinfo=self.tz)

    def test_settle_booking_consume_decrements_settled_and_reserved_and_commits_entry(
        self,
    ):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student,
            start_at=self.slot_1,
            lesson_option="regular_50",
        )

        entry = settle_booking(
            tenant=self.tenant,
            booking=booking,
            action="consume",
            actor=self.teacher_user,
            reason=Booking.SettlementReason.COMPLETED,
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.CONSUME)
        self.assertEqual(entry.amount, Decimal("20.00"))
        self.assertEqual(entry.settled_after, Decimal("30.00"))
        self.assertEqual(entry.reserved_after, Decimal("0.00"))
        self.assertEqual(
            entry.idempotency_key,
            f"tenant:{self.tenant.id}:booking:{booking.id}:settle",
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("30.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("30.00"))

        booking.refresh_from_db()
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.CONSUMED)
        self.assertEqual(booking.settlement_reason, Booking.SettlementReason.COMPLETED)

    def test_settle_booking_release_decrements_reserved_only_and_commits_entry(self):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student,
            start_at=self.slot_2,
            lesson_option="regular_50",
        )

        entry = settle_booking(
            tenant=self.tenant,
            booking=booking,
            action="release",
            actor=self.teacher_user,
            reason=Booking.SettlementReason.TEACHER_CANCELLED,
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.RELEASE)
        self.assertEqual(entry.amount, Decimal("20.00"))
        self.assertEqual(entry.settled_after, Decimal("50.00"))
        self.assertEqual(entry.reserved_after, Decimal("0.00"))
        self.assertEqual(
            entry.idempotency_key,
            f"tenant:{self.tenant.id}:booking:{booking.id}:settle",
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("50.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("50.00"))

        booking.refresh_from_db()
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            booking.settlement_reason, Booking.SettlementReason.TEACHER_CANCELLED
        )

    def test_price_change_after_booking_settles_exact_held_amount(self):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student,
            start_at=self.slot_1,
            lesson_option="regular_50",
        )
        self.assertEqual(booking.held_amount, Decimal("20.00"))

        # Teacher subsequently raises hourly rate to $35.00
        self.teacher.lesson_price = Decimal("35.00")
        self.teacher.save(update_fields=["lesson_price"])

        entry = settle_booking(
            tenant=self.tenant,
            booking=booking,
            action="consume",
            actor=self.teacher_user,
            reason=Booking.SettlementReason.COMPLETED,
        )

        self.assertEqual(entry.amount, Decimal("20.00"))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("30.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_post_lock_non_reserved_returns_none_idempotently(self):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student,
            start_at=self.slot_1,
            lesson_option="regular_50",
        )
        first_entry = settle_booking(
            tenant=self.tenant,
            booking=booking,
            action="consume",
            actor=self.teacher_user,
            reason=Booking.SettlementReason.COMPLETED,
        )
        self.assertIsNotNone(first_entry)

        # Second call with a stale in-memory booking object must reload post-lock and return None
        stale_booking = Booking(
            id=booking.id,
            teacher=self.teacher,
            student=self.student,
            start_at=booking.start_at,
            end_at=booking.end_at,
            payment_status=Booking.PaymentStatus.RESERVED,
            held_amount=Decimal("20.00"),
        )
        second_entry = settle_booking(
            tenant=self.tenant,
            booking=stale_booking,
            action="release",
            actor=self.teacher_user,
            reason=Booking.SettlementReason.TEACHER_CANCELLED,
        )
        self.assertIsNone(second_entry)
        self.assertEqual(
            LedgerEntry.objects.filter(
                idempotency_key=f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
            ).count(),
            1,
        )

        # Other non-RESERVED statuses also cleanly return None
        for idx, status in enumerate(
            [
                Booking.PaymentStatus.NOT_REQUIRED,
                Booking.PaymentStatus.UNPAID,
                Booking.PaymentStatus.LEGACY_EXTERNAL,
                Booking.PaymentStatus.RELEASED,
            ],
            start=1,
        ):
            start_at = timezone.now() + timedelta(days=5 + idx)
            b = Booking.objects.create(
                student=self.student,
                teacher=self.teacher,
                start_at=start_at,
                end_at=start_at + timedelta(minutes=50),
                payment_status=status,
                held_amount=Decimal("0.00"),
            )
            res = settle_booking(
                tenant=self.tenant,
                booking=b,
                action="release",
                actor=None,
                reason=Booking.SettlementReason.EXPIRED_PENDING,
            )
            self.assertIsNone(res)

    def test_frozen_wallet_permits_both_consume_and_release(self):
        b_consume = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student,
            start_at=self.slot_1,
            lesson_option="regular_50",
        )
        b_release = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student,
            start_at=self.slot_2,
            lesson_option="regular_50",
        )

        self.wallet.is_frozen = True
        self.wallet.save(update_fields=["is_frozen"])

        consume_entry = settle_booking(
            tenant=self.tenant,
            booking=b_consume,
            action="consume",
            actor=self.teacher_user,
            reason=Booking.SettlementReason.COMPLETED,
        )
        release_entry = settle_booking(
            tenant=self.tenant,
            booking=b_release,
            action="release",
            actor=self.teacher_user,
            reason=Booking.SettlementReason.TEACHER_CANCELLED,
        )

        self.assertEqual(consume_entry.entry_type, LedgerEntry.EntryType.CONSUME)
        self.assertEqual(release_entry.entry_type, LedgerEntry.EntryType.RELEASE)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("30.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_unified_atomic_rollback_reverts_booking_and_wallet_on_ledger_error(self):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student,
            start_at=self.slot_1,
            lesson_option="regular_50",
        )

        with patch(
            "wallet.services.LedgerEntry.objects.create",
            side_effect=RuntimeError("Simulated ledger failure"),
        ):
            with self.assertRaises(RuntimeError):
                settle_booking(
                    tenant=self.tenant,
                    booking=booking,
                    action="consume",
                    actor=self.teacher_user,
                    reason=Booking.SettlementReason.COMPLETED,
                )

        booking.refresh_from_db()
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RESERVED)
        self.assertIsNone(booking.settlement_reason)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("50.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("20.00"))
        self.assertEqual(
            LedgerEntry.objects.filter(
                entry_type=LedgerEntry.EntryType.CONSUME
            ).count(),
            0,
        )

    def test_validates_action_reason_and_tenant_mismatch(self):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student,
            start_at=self.slot_1,
            lesson_option="regular_50",
        )

        with self.assertRaises(ValueError):
            settle_booking(
                tenant=self.tenant,
                booking=booking,
                action="invalid",
                actor=self.teacher_user,
                reason=Booking.SettlementReason.COMPLETED,
            )

        with self.assertRaises(ValueError):
            settle_booking(
                tenant=self.tenant,
                booking=booking,
                action="consume",
                actor=self.teacher_user,
                reason="NOT_A_REAL_REASON",
            )

        other_teacher = User.objects.create_user(
            username="other_teacher_settle",
            password="password123",
            role=User.Role.TEACHER,
        )
        other_tenant = Tenant.objects.get(teacher_profile=other_teacher.teacher_profile)
        with self.assertRaises(TenantMismatchError):
            settle_booking(
                tenant=other_tenant,
                booking=booking,
                action="consume",
                actor=self.teacher_user,
                reason=Booking.SettlementReason.COMPLETED,
            )

    def test_accepts_all_11_settlement_reasons(self):
        self.assertEqual(len(Booking.SettlementReason.values), 11)
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("250.00"),
            idempotency_key="settle-seed-250",
            reference_id="seed_250",
            description="Extra funds for 11 reasons",
        )
        for idx, reason in enumerate(Booking.SettlementReason.values):
            day = timezone.localdate() + timedelta(days=10 + idx)
            RegularAvailability.objects.get_or_create(
                teacher=self.teacher,
                day_of_week=day.weekday(),
                start_time=time(9, 0),
                end_time=time(17, 0),
            )
            slot = timezone.datetime.combine(day, time(10, 0), tzinfo=self.tz)
            booking = create_booking_with_hold(
                teacher=self.teacher,
                student=self.student,
                start_at=slot,
                lesson_option="regular_50",
            )
            action = (
                "consume"
                if reason
                in {
                    Booking.SettlementReason.COMPLETED,
                    Booking.SettlementReason.LATE_CANCEL,
                    Booking.SettlementReason.STUDENT_ABSENT,
                    Booking.SettlementReason.DISPUTE_TEACHER_FAVOR,
                }
                else "release"
            )
            entry = settle_booking(
                tenant=self.tenant,
                booking=booking,
                action=action,
                actor=None,
                reason=reason,
            )
            self.assertIsNotNone(entry)
            booking.refresh_from_db()
            self.assertEqual(booking.settlement_reason, reason)
