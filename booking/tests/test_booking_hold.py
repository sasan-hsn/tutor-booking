from datetime import time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Tenant, TermsAcceptance, User
from booking.models import Booking, RegularAvailability
from booking.services import create_booking_with_hold
from wallet.exceptions import (
    CurrencyMismatchError,
    InsufficientBalanceError,
    WalletFrozenError,
)
from wallet.models import LedgerEntry, Wallet
from wallet.services import credit_wallet, get_or_create_wallet


class BookingPaymentFieldsTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_fields",
            email="teacher_fields@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.student_user = User.objects.create_user(
            username="student_fields",
            email="student_fields@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

    def test_booking_has_payment_and_dispute_fields_with_defaults(self):
        start_at = timezone.now() + timedelta(days=1)
        booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher_user.teacher_profile,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=50),
        )

        self.assertEqual(booking.payment_status, Booking.PaymentStatus.UNPAID)
        self.assertEqual(booking.payment_status, "UNPAID")
        self.assertEqual(booking.held_amount, Decimal("0.00"))
        self.assertIsNone(booking.settlement_reason)
        self.assertIsNone(booking.dispute_opened_at)
        self.assertIsNone(booking.dispute_resolved_by)
        self.assertIsNone(booking.dispute_resolution)

        expected_choices = {
            "LEGACY_EXTERNAL",
            "NOT_REQUIRED",
            "UNPAID",
            "RESERVED",
            "CONSUMED",
            "RELEASED",
        }
        actual_choices = {choice[0] for choice in Booking.PaymentStatus.choices}
        self.assertEqual(actual_choices, expected_choices)


class BookingDeletionProtectionTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_del",
            email="teacher_del@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.student_user = User.objects.create_user(
            username="student_del",
            email="student_del@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

    def _make_booking(self, offset_days, payment_status):
        start_at = timezone.now() + timedelta(days=offset_days)
        return Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher_user.teacher_profile,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=50),
            payment_status=payment_status,
            held_amount=(
                Decimal("20.00")
                if payment_status == Booking.PaymentStatus.RESERVED
                else Decimal("0.00")
            ),
        )

    def test_instance_delete_reserved_booking_raises_validation_error(self):
        booking = self._make_booking(1, Booking.PaymentStatus.RESERVED)

        with self.assertRaisesMessage(
            ValidationError, "Cannot delete booking with active funds hold"
        ):
            booking.delete()

        self.assertTrue(Booking.objects.filter(pk=booking.pk).exists())

    def test_queryset_delete_reserved_booking_is_all_or_nothing(self):
        reserved = self._make_booking(1, Booking.PaymentStatus.RESERVED)
        unpaid = self._make_booking(2, Booking.PaymentStatus.UNPAID)

        with self.assertRaisesMessage(
            ValidationError, "Cannot delete booking with active funds hold"
        ):
            Booking.objects.filter(pk__in=[reserved.pk, unpaid.pk]).delete()

        self.assertTrue(Booking.objects.filter(pk=reserved.pk).exists())
        self.assertTrue(Booking.objects.filter(pk=unpaid.pk).exists())

    def test_delete_non_reserved_bookings_succeeds(self):
        deletable_statuses = [
            Booking.PaymentStatus.UNPAID,
            Booking.PaymentStatus.NOT_REQUIRED,
            Booking.PaymentStatus.LEGACY_EXTERNAL,
            Booking.PaymentStatus.RELEASED,
            Booking.PaymentStatus.CONSUMED,
        ]
        bookings = [
            self._make_booking(idx + 1, status)
            for idx, status in enumerate(deletable_statuses)
        ]

        # Instance delete on first
        bookings[0].delete()
        self.assertFalse(Booking.objects.filter(pk=bookings[0].pk).exists())

        # QuerySet delete on remaining
        remaining_pks = [b.pk for b in bookings[1:]]
        Booking.objects.filter(pk__in=remaining_pks).delete()
        self.assertEqual(Booking.objects.filter(pk__in=remaining_pks).count(), 0)

    def test_cascade_delete_reserved_booking_raises_validation_error(self):
        booking = self._make_booking(1, Booking.PaymentStatus.RESERVED)

        with self.assertRaisesMessage(
            ValidationError, "Cannot delete booking with active funds hold"
        ):
            with transaction.atomic():
                self.student_user.delete()

        self.assertTrue(Booking.objects.filter(pk=booking.pk).exists())


class BookingAdminDeletionProtectionTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="admin_booking",
            email="admin_booking@example.com",
            password="adminpassword",
        )
        self.teacher_user = User.objects.create_user(
            username="teacher_admin_del",
            email="teacher_admin_del@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.student_user = User.objects.create_user(
            username="student_admin_del",
            email="student_admin_del@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.client.force_login(self.superuser)

    def _make_booking(self, offset_days, payment_status):
        start_at = timezone.now() + timedelta(days=offset_days)
        return Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher_user.teacher_profile,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=50),
            payment_status=payment_status,
            held_amount=(
                Decimal("20.00")
                if payment_status == Booking.PaymentStatus.RESERVED
                else Decimal("0.00")
            ),
        )

    def test_admin_bulk_delete_action_blocks_reserved_booking_with_error_message(self):
        reserved = self._make_booking(1, Booking.PaymentStatus.RESERVED)
        unpaid = self._make_booking(2, Booking.PaymentStatus.UNPAID)

        changelist_url = reverse("admin:booking_booking_changelist")
        response = self.client.post(
            changelist_url,
            {
                "action": "delete_selected",
                "_selected_action": [str(reserved.pk), str(unpaid.pk)],
                "post": "yes",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Booking.objects.filter(pk=reserved.pk).exists())
        self.assertTrue(Booking.objects.filter(pk=unpaid.pk).exists())
        msgs = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("active funds hold" in str(m).lower() for m in msgs),
            f"Expected error message about active funds hold, got: {[str(m) for m in msgs]}",
        )
        self.assertFalse(
            any("successfully deleted" in str(m).lower() for m in msgs),
            f"Unexpected success message: {[str(m) for m in msgs]}",
        )

    def test_admin_single_object_delete_blocks_reserved_booking_gracefully(self):
        reserved = self._make_booking(1, Booking.PaymentStatus.RESERVED)

        delete_url = reverse("admin:booking_booking_delete", args=[reserved.pk])
        response = self.client.post(delete_url, {"post": "yes"}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Booking.objects.filter(pk=reserved.pk).exists())
        msgs = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("active funds hold" in str(m).lower() for m in msgs),
            f"Expected error message about active funds hold, got: {[str(m) for m in msgs]}",
        )
        self.assertFalse(
            any("was deleted successfully" in str(m).lower() for m in msgs),
            f"Unexpected success message: {[str(m) for m in msgs]}",
        )

    def test_admin_delete_non_reserved_booking_succeeds(self):
        unpaid = self._make_booking(1, Booking.PaymentStatus.UNPAID)

        changelist_url = reverse("admin:booking_booking_changelist")
        response = self.client.post(
            changelist_url,
            {
                "action": "delete_selected",
                "_selected_action": [str(unpaid.pk)],
                "post": "yes",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Booking.objects.filter(pk=unpaid.pk).exists())


class BookingZeroPriceAndLegacyNoOpTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_noop",
            email="teacher_noop@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.lesson_price = Decimal("25.00")
        self.teacher.lesson_price_25 = Decimal("15.00")
        self.teacher.offers_trial = True
        self.teacher.trial_price = Decimal("0.00")
        self.teacher.save()

        self.tenant = Tenant.objects.get(teacher_profile=self.teacher)
        self.tenant.payments_enabled = True
        self.tenant.save()

        self.student_user = User.objects.create_user(
            username="student_noop",
            email="student_noop@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

        self.tz = ZoneInfo(self.teacher_user.timezone)
        self.future_day = timezone.localdate() + timedelta(days=2)
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=self.future_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        self.slot_start = timezone.datetime.combine(
            self.future_day, time(10, 0), tzinfo=self.tz
        )

    def test_zero_price_booking_sets_not_required_and_bypasses_wallet(self):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=self.slot_start,
            lesson_option="trial",
        )

        self.assertEqual(booking.price, Decimal("0.00"))
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.NOT_REQUIRED)
        self.assertEqual(booking.held_amount, Decimal("0.00"))
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.assertEqual(Wallet.objects.filter(user=self.student_user).count(), 0)

    def test_payments_disabled_sets_unpaid_and_bypasses_wallet(self):
        self.tenant.payments_enabled = False
        self.tenant.save()

        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=self.slot_start,
            lesson_option="regular_50",
        )

        self.assertEqual(booking.price, Decimal("25.00"))
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.UNPAID)
        self.assertEqual(booking.held_amount, Decimal("0.00"))
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.assertEqual(Wallet.objects.filter(user=self.student_user).count(), 0)

    def test_teacher_without_tenant_graceful_fallback_sets_unpaid(self):
        self.tenant.delete()
        self.teacher.refresh_from_db()

        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=self.slot_start,
            lesson_option="regular_50",
        )

        self.assertEqual(booking.payment_status, Booking.PaymentStatus.UNPAID)
        self.assertEqual(booking.held_amount, Decimal("0.00"))
        self.assertEqual(LedgerEntry.objects.count(), 0)

    def test_legacy_external_booking_completes_and_cancels_without_ledger_entries(self):
        past_start = timezone.now() - timedelta(hours=3)
        completed_target = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=past_start,
            end_at=past_start + timedelta(minutes=50),
            status=Booking.Status.CONFIRMED,
            price=Decimal("25.00"),
            payment_status=Booking.PaymentStatus.LEGACY_EXTERNAL,
        )
        future_start = timezone.now() + timedelta(days=3)
        cancel_target = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=future_start,
            end_at=future_start + timedelta(minutes=50),
            status=Booking.Status.CONFIRMED,
            price=Decimal("25.00"),
            payment_status=Booking.PaymentStatus.LEGACY_EXTERNAL,
        )

        teacher_client = self.client_class()
        teacher_client.force_login(self.teacher_user)

        resp1 = teacher_client.post(
            reverse(
                "booking:complete_lesson", kwargs={"booking_id": completed_target.id}
            )
        )
        self.assertEqual(resp1.status_code, 200)
        completed_target.refresh_from_db()
        self.assertEqual(completed_target.status, Booking.Status.COMPLETED)
        self.assertEqual(
            completed_target.payment_status, Booking.PaymentStatus.LEGACY_EXTERNAL
        )

        resp2 = teacher_client.post(
            reverse("booking:cancel_lesson", kwargs={"booking_id": cancel_target.id})
        )
        self.assertEqual(resp2.status_code, 200)
        cancel_target.refresh_from_db()
        self.assertEqual(cancel_target.status, Booking.Status.CANCELLED)
        self.assertEqual(
            cancel_target.payment_status, Booking.PaymentStatus.LEGACY_EXTERNAL
        )
        self.assertEqual(LedgerEntry.objects.count(), 0)


class BookingHoldEnforcementServiceTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_hold",
            email="teacher_hold@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.lesson_price = Decimal("25.00")
        self.teacher.lesson_price_25 = Decimal("15.00")
        self.teacher.offers_trial = True
        self.teacher.trial_price = Decimal("0.00")
        self.teacher.save()

        self.tenant = Tenant.objects.get(teacher_profile=self.teacher)
        self.tenant.payments_enabled = True
        self.tenant.save()

        self.student_user = User.objects.create_user(
            username="student_hold",
            email="student_hold@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

        self.tz = ZoneInfo(self.teacher_user.timezone)
        self.future_day = timezone.localdate() + timedelta(days=2)
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=self.future_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        self.slot_start = timezone.datetime.combine(
            self.future_day, time(10, 0), tzinfo=self.tz
        )

    def _fund_student(self, amount, currency="USD"):
        wallet = get_or_create_wallet(self.tenant, self.student_user, currency=currency)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=amount,
            idempotency_key=f"seed-{self.student_user.id}-{currency}-{amount}",
            reference_id="seed_topup",
            description="Seed wallet balance",
        )
        return wallet

    def test_paid_booking_reserves_funds_and_marks_reserved(self):
        wallet = self._fund_student(Decimal("50.00"))

        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=self.slot_start,
            lesson_option="regular_50",
        )

        booking.refresh_from_db()
        wallet.refresh_from_db()
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RESERVED)
        self.assertEqual(booking.held_amount, Decimal("25.00"))
        self.assertEqual(wallet.settled_balance, Decimal("50.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("25.00"))
        self.assertEqual(wallet.available_balance, Decimal("25.00"))

        reserve_entries = LedgerEntry.objects.filter(
            wallet=wallet,
            entry_type=LedgerEntry.EntryType.RESERVE,
        )
        self.assertEqual(reserve_entries.count(), 1)
        entry = reserve_entries.first()
        self.assertEqual(entry.amount, Decimal("25.00"))
        self.assertEqual(
            entry.idempotency_key,
            f"tenant:{self.tenant.id}:booking:{booking.id}:reserve",
        )

    def test_insufficient_balance_rolls_back_with_zero_residual_rows(self):
        wallet = self._fund_student(Decimal("10.00"))

        with self.assertRaises(InsufficientBalanceError):
            create_booking_with_hold(
                teacher=self.teacher,
                student=self.student_user,
                start_at=self.slot_start,
                lesson_option="regular_50",
            )

        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(
            LedgerEntry.objects.filter(
                entry_type=LedgerEntry.EntryType.RESERVE
            ).count(),
            0,
        )
        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(wallet.available_balance, Decimal("10.00"))

    def test_frozen_wallet_rejects_booking_with_clean_rollback(self):
        wallet = self._fund_student(Decimal("50.00"))
        wallet.is_frozen = True
        wallet.save(update_fields=["is_frozen"])

        with self.assertRaises(WalletFrozenError):
            create_booking_with_hold(
                teacher=self.teacher,
                student=self.student_user,
                start_at=self.slot_start,
                lesson_option="regular_50",
            )

        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(
            LedgerEntry.objects.filter(
                entry_type=LedgerEntry.EntryType.RESERVE
            ).count(),
            0,
        )
        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))

    def test_currency_mismatch_eur_wallet_on_usd_tenant_rejected(self):
        self._fund_student(Decimal("50.00"), currency="EUR")

        with self.assertRaises(CurrencyMismatchError):
            create_booking_with_hold(
                teacher=self.teacher,
                student=self.student_user,
                start_at=self.slot_start,
                lesson_option="regular_50",
            )

        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(
            LedgerEntry.objects.filter(
                entry_type=LedgerEntry.EntryType.RESERVE
            ).count(),
            0,
        )

    def test_currency_mismatch_usd_wallet_on_eur_tenant_rejected(self):
        self._fund_student(Decimal("50.00"), currency="USD")
        self.tenant.default_currency = "EUR"
        self.tenant.save()

        with self.assertRaises(CurrencyMismatchError):
            create_booking_with_hold(
                teacher=self.teacher,
                student=self.student_user,
                start_at=self.slot_start,
                lesson_option="regular_50",
            )

        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(
            LedgerEntry.objects.filter(
                entry_type=LedgerEntry.EntryType.RESERVE
            ).count(),
            0,
        )


class BookSlotHoldViewTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_view_hold",
            email="teacher_view_hold@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.lesson_price = Decimal("25.00")
        self.teacher.lesson_price_25 = Decimal("15.00")
        self.teacher.offers_trial = True
        self.teacher.trial_price = Decimal("0.00")
        self.teacher.save()

        self.tenant = Tenant.objects.get(teacher_profile=self.teacher)
        self.tenant.payments_enabled = True
        self.tenant.save()

        self.student_user = User.objects.create_user(
            username="student_view_hold",
            email="student_view_hold@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.student_client = self.client_class()
        self.student_client.force_login(self.student_user)
        TermsAcceptance.objects.create(
            user=self.student_user,
            tenant=self.tenant,
            version=settings.TERMS_CURRENT_VERSION,
            ip_address="127.0.0.1",
            user_agent="TestSuite",
        )

        self.tz = ZoneInfo(self.teacher_user.timezone)
        self.future_day = timezone.localdate() + timedelta(days=2)
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=self.future_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        self.slot_start = timezone.datetime.combine(
            self.future_day, time(10, 0), tzinfo=self.tz
        )
        self.book_url = reverse("booking:book_slot")

    def _fund_student(self, amount, currency="USD"):
        wallet = get_or_create_wallet(self.tenant, self.student_user, currency=currency)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=amount,
            idempotency_key=f"view-seed-{self.student_user.id}-{currency}-{amount}",
            reference_id="view_seed_topup",
            description="View seed wallet balance",
        )
        return wallet

    def test_book_slot_insufficient_balance_returns_402(self):
        self._fund_student(Decimal("10.00"))
        response = self.student_client.post(
            self.book_url,
            {"start_at": self.slot_start.isoformat(), "lesson_option": "regular_50"},
        )
        self.assertEqual(response.status_code, 402)
        self.assertIn("error", response.json())
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(
            LedgerEntry.objects.filter(
                entry_type=LedgerEntry.EntryType.RESERVE
            ).count(),
            0,
        )

    def test_book_slot_frozen_wallet_returns_403(self):
        wallet = self._fund_student(Decimal("50.00"))
        wallet.is_frozen = True
        wallet.save(update_fields=["is_frozen"])

        response = self.student_client.post(
            self.book_url,
            {"start_at": self.slot_start.isoformat(), "lesson_option": "regular_50"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())
        self.assertEqual(Booking.objects.count(), 0)

    def test_book_slot_currency_mismatch_returns_400(self):
        self._fund_student(Decimal("50.00"), currency="EUR")

        response = self.student_client.post(
            self.book_url,
            {"start_at": self.slot_start.isoformat(), "lesson_option": "regular_50"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        self.assertEqual(Booking.objects.count(), 0)

    def test_book_slot_paid_success_reserves_funds(self):
        wallet = self._fund_student(Decimal("50.00"))

        response = self.student_client.post(
            self.book_url,
            {"start_at": self.slot_start.isoformat(), "lesson_option": "regular_50"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        booking = Booking.objects.get(pk=response.json()["booking_id"])
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RESERVED)
        self.assertEqual(booking.held_amount, Decimal("25.00"))
        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_balance, Decimal("25.00"))

    def test_book_slot_zero_price_trial_succeeds_without_wallet(self):
        response = self.student_client.post(
            self.book_url,
            {"start_at": self.slot_start.isoformat(), "lesson_option": "trial"},
        )
        self.assertEqual(response.status_code, 200)
        booking = Booking.objects.get(pk=response.json()["booking_id"])
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.NOT_REQUIRED)
        self.assertEqual(booking.held_amount, Decimal("0.00"))
        self.assertEqual(LedgerEntry.objects.count(), 0)
