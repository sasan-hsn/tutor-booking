from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from accounts.models import Tenant, User
from booking.models import Booking
from wallet.exceptions import TenantMismatchError
from wallet.models import LedgerEntry
from wallet.services import (
    credit_wallet,
    get_or_create_wallet,
    reserve_funds,
    settle_booking,
    waive_penalty,
)


class WaivePenaltyTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_waive",
            email="teacher_waive@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher = self.teacher_user.teacher_profile
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher)
        self.tenant.payments_enabled = True
        self.tenant.save()

        self.other_teacher_user = User.objects.create_user(
            username="other_teacher_waive",
            email="other_teacher_waive@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.other_tenant = Tenant.objects.get(
            teacher_profile=self.other_teacher_user.teacher_profile
        )

        self.student_user = User.objects.create_user(
            username="student_waive",
            email="student_waive@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="waive-seed",
            reference_id="waive_seed",
            description="Seed $100.00",
        )

    def _create_settled_booking(
        self,
        *,
        action: str,
        reason: str,
        status: str = Booking.Status.CANCELLED,
        price: Decimal = Decimal("25.00"),
    ) -> Booking:
        start = timezone.now() + timedelta(hours=5)
        booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start,
            end_at=start + timedelta(minutes=50),
            status=status,
            price=price,
            payment_status=Booking.PaymentStatus.RESERVED,
            held_amount=price,
        )
        reserve_funds(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=price,
            idempotency_key=f"tenant:{self.tenant.id}:booking:{booking.id}:reserve",
            reference_id=f"booking_{booking.id}",
            description=f"Booking hold #{booking.id}",
        )
        settle_booking(
            tenant=self.tenant,
            booking=booking,
            action=action,
            actor=self.teacher_user,
            reason=reason,
        )
        booking.refresh_from_db()
        self.wallet.refresh_from_db()
        return booking

    def test_waive_penalty_on_late_cancel_restores_balance_and_creates_waiver(self):
        booking = self._create_settled_booking(
            action="consume",
            reason=Booking.SettlementReason.LATE_CANCEL,
        )
        self.assertEqual(self.wallet.settled_balance, Decimal("75.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

        entry = waive_penalty(
            tenant=self.tenant,
            booking=booking,
            teacher_user=self.teacher_user,
        )

        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.WAIVER)
        self.assertEqual(entry.amount, Decimal("25.00"))
        self.assertEqual(
            entry.idempotency_key,
            f"tenant:{self.tenant.id}:booking:{booking.id}:waiver",
        )
        self.assertEqual(entry.settled_after, Decimal("100.00"))
        self.assertEqual(entry.reserved_after, Decimal("0.00"))

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("100.00"))

    def test_waive_penalty_on_student_absent_restores_balance(self):
        booking = self._create_settled_booking(
            action="consume",
            reason=Booking.SettlementReason.STUDENT_ABSENT,
        )
        entry = waive_penalty(
            tenant=self.tenant,
            booking=booking,
            teacher_user=self.teacher_user,
            reason="Medical emergency waiver",
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.WAIVER)
        self.assertEqual(entry.amount, Decimal("25.00"))
        self.assertIn("Medical emergency waiver", entry.description)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))

    def test_waive_penalty_on_completed_booking_raises_validation_error(self):
        booking = self._create_settled_booking(
            action="consume",
            reason=Booking.SettlementReason.COMPLETED,
            status=Booking.Status.COMPLETED,
        )
        with self.assertRaises(ValidationError):
            waive_penalty(
                tenant=self.tenant,
                booking=booking,
                teacher_user=self.teacher_user,
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("75.00"))

    def test_waive_penalty_on_released_booking_raises_validation_error(self):
        booking = self._create_settled_booking(
            action="release",
            reason=Booking.SettlementReason.STUDENT_CANCELLED,
        )
        with self.assertRaises(ValidationError):
            waive_penalty(
                tenant=self.tenant,
                booking=booking,
                teacher_user=self.teacher_user,
            )

    def test_waive_penalty_second_time_raises_integrity_error(self):
        booking = self._create_settled_booking(
            action="consume",
            reason=Booking.SettlementReason.LATE_CANCEL,
        )
        waive_penalty(
            tenant=self.tenant,
            booking=booking,
            teacher_user=self.teacher_user,
        )

        with self.assertRaises(IntegrityError):
            waive_penalty(
                tenant=self.tenant,
                booking=booking,
                teacher_user=self.teacher_user,
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))

    def test_waive_penalty_enforces_tenant_and_teacher_authorization(self):
        booking = self._create_settled_booking(
            action="consume",
            reason=Booking.SettlementReason.LATE_CANCEL,
        )

        with self.assertRaises(TenantMismatchError):
            waive_penalty(
                tenant=self.other_tenant,
                booking=booking,
                teacher_user=self.other_teacher_user,
            )

        with self.assertRaises(ValidationError):
            waive_penalty(
                tenant=self.tenant,
                booking=booking,
                teacher_user=self.other_teacher_user,
            )
