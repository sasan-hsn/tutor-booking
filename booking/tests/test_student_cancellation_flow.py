from datetime import time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Tenant, User
from booking.models import Booking, RegularAvailability
from booking.services import create_booking_with_hold
from wallet.models import LedgerEntry
from wallet.services import credit_wallet, get_or_create_wallet


class StudentDirectCancellationTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_cancel_flow",
            email="teacher_cancel_flow@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.lesson_price = Decimal("25.00")
        self.teacher.lesson_price_25 = Decimal("15.00")
        self.teacher.offers_trial = False
        self.teacher.instant_tutoring_enabled = True
        self.teacher.save()

        self.tenant = Tenant.objects.get(teacher_profile=self.teacher)
        self.tenant.payments_enabled = True
        self.tenant.save()

        self.student_user = User.objects.create_user(
            username="student_cancel_flow",
            email="student_cancel_flow@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="cancel-flow-seed",
            reference_id="cancel_seed",
            description="Seed $100.00",
        )

        self.student_client = self.client_class()
        self.student_client.force_login(self.student_user)
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

    def _create_reserved_booking(self, *, status: str, start_at):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=self.future_slot,
            lesson_option="regular_50",
        )
        booking.status = status
        booking.start_at = start_at
        booking.end_at = start_at + timedelta(minutes=50)
        booking.save()
        return booking

    def test_pending_booking_cancelled_directly_by_student_releases_hold(self):
        # Even within 24h, an unconfirmed PENDING booking cancels directly with zero fee
        booking = self._create_reserved_booking(
            status=Booking.Status.PENDING,
            start_at=timezone.now() + timedelta(hours=2),
        )

        detail_resp = self.student_client.get(
            reverse("booking:lesson_detail_student", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(detail_resp.status_code, 200)
        cancel_url = reverse(
            "booking:cancel_lesson_student", kwargs={"booking_id": booking.id}
        )
        self.assertContains(detail_resp, f'data-cancel-url="{cancel_url}"')
        self.assertNotContains(detail_resp, "requestCancellationBtn")

        resp = self.student_client.post(cancel_url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            booking.settlement_reason, Booking.SettlementReason.STUDENT_CANCELLED
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

        entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.RELEASE)

    def test_timely_confirmed_cancellation_at_24h_1m_releases_hold(self):
        booking = self._create_reserved_booking(
            status=Booking.Status.CONFIRMED,
            start_at=timezone.now() + timedelta(hours=24, minutes=1),
        )

        detail_resp = self.student_client.get(
            reverse("booking:lesson_detail_student", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(detail_resp.status_code, 200)
        cancel_url = reverse(
            "booking:cancel_lesson_student", kwargs={"booking_id": booking.id}
        )
        self.assertContains(detail_resp, f'data-cancel-url="{cancel_url}"')
        self.assertNotContains(detail_resp, "requestCancellationBtn")

        resp = self.student_client.post(cancel_url)
        self.assertEqual(resp.status_code, 200)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            booking.settlement_reason, Booking.SettlementReason.STUDENT_CANCELLED
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_short_notice_at_23h_59m_blocks_direct_cancel_and_requires_request(self):
        booking = self._create_reserved_booking(
            status=Booking.Status.CONFIRMED,
            start_at=timezone.now() + timedelta(hours=23, minutes=59),
        )

        # Direct cancel endpoint rejects <24h confirmed booking
        cancel_url = reverse(
            "booking:cancel_lesson_student", kwargs={"booking_id": booking.id}
        )
        direct_resp = self.student_client.post(cancel_url)
        self.assertEqual(direct_resp.status_code, 400)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RESERVED)

        # Detail modal disables direct cancel and presents Request Cancellation + forfeiture warning
        detail_resp = self.student_client.get(
            reverse("booking:lesson_detail_student", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(detail_resp.status_code, 200)
        self.assertNotContains(detail_resp, 'id="cancelLessonBtn"')
        self.assertContains(detail_resp, 'id="requestCancellationBtn"')
        self.assertContains(
            detail_resp,
            "Cancelling less than 24 hours in advance forfeits the lesson fee unless waived by your teacher.",
        )

        # Submitting cancellation request sets cancellation_requested=True
        req_url = reverse(
            "booking:request_cancellation", kwargs={"booking_id": booking.id}
        )
        req_resp = self.student_client.post(req_url)
        self.assertEqual(req_resp.status_code, 200)
        booking.refresh_from_db()
        self.assertTrue(booking.cancellation_requested)
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RESERVED)

    def test_request_cancellation_rejected_more_than_24h_before_start(self):
        booking = self._create_reserved_booking(
            status=Booking.Status.CONFIRMED,
            start_at=timezone.now() + timedelta(hours=25),
        )
        req_url = reverse(
            "booking:request_cancellation", kwargs={"booking_id": booking.id}
        )
        resp = self.student_client.post(req_url)
        self.assertEqual(resp.status_code, 400)
        booking.refresh_from_db()
        self.assertFalse(booking.cancellation_requested)

    def test_teacher_accepting_late_cancellation_consumes_hold_as_late_cancel(self):
        booking = self._create_reserved_booking(
            status=Booking.Status.CONFIRMED,
            start_at=timezone.now() + timedelta(hours=12),
        )
        req_resp = self.student_client.post(
            reverse("booking:request_cancellation", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(req_resp.status_code, 200)

        accept_resp = self.teacher_client.post(
            reverse("booking:respond_to_booking", kwargs={"booking_id": booking.id}),
            {"action": "accept"},
        )
        self.assertEqual(accept_resp.status_code, 200)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertFalse(booking.cancellation_requested)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.CONSUMED)
        self.assertEqual(
            booking.settlement_reason, Booking.SettlementReason.LATE_CANCEL
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("75.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

        entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.CONSUME)
        self.assertEqual(entry.amount, Decimal("25.00"))


class UnansweredCancellationResolutionTests(TestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_unanswered",
            email="teacher_unanswered@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.lesson_price = Decimal("25.00")
        self.teacher.offers_trial = False
        self.teacher.save()

        self.tenant = Tenant.objects.get(teacher_profile=self.teacher)
        self.tenant.payments_enabled = True
        self.tenant.save()

        self.student_user = User.objects.create_user(
            username="student_unanswered",
            email="student_unanswered@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="unanswered-seed",
            reference_id="unanswered_seed",
            description="Seed $100.00",
        )

        self.teacher_client = self.client_class()
        self.teacher_client.force_login(self.teacher_user)
        self.student_client = self.client_class()
        self.student_client.force_login(self.student_user)

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

    def _create_unanswered_cancellation_at_start(self, *, minutes_since_start=0):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=self.future_slot,
            lesson_option="regular_50",
        )
        start_at = timezone.now() - timedelta(minutes=minutes_since_start)
        booking.status = Booking.Status.CONFIRMED
        booking.cancellation_requested = True
        booking.start_at = start_at
        booking.end_at = start_at + timedelta(minutes=50)
        booking.save()
        return booking

    def test_unanswered_cancellation_at_start_is_awaiting_resolution_and_blocks_complete(
        self,
    ):
        # At lesson start (even before end_at), unanswered cancellation is awaiting resolution
        booking = self._create_unanswered_cancellation_at_start(minutes_since_start=1)
        self.assertTrue(booking.is_awaiting_resolution)

        # Teacher modal blocks Mark Completed and passes fault_party="teacher" on Not Held
        teacher_modal = self.teacher_client.get(
            reverse("booking:lesson_detail", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(teacher_modal.status_code, 200)
        self.assertNotContains(teacher_modal, 'id="completeLessonBtn"')
        self.assertContains(teacher_modal, 'id="markNotHeldBtn"')
        self.assertContains(teacher_modal, 'data-fault-party="teacher"')

        # Student modal does not claim the lesson has ended before end_at
        student_modal = self.student_client.get(
            reverse("booking:lesson_detail_student", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(student_modal.status_code, 200)
        self.assertNotContains(student_modal, "This lesson has ended")
        self.assertContains(student_modal, "awaiting your teacher's resolution")

        # Even after end_at passes, teacher cannot complete while cancellation_requested=True
        booking.start_at = timezone.now() - timedelta(hours=2)
        booking.end_at = timezone.now() - timedelta(hours=1)
        booking.save()
        self.assertTrue(booking.is_awaiting_resolution)

        complete_resp = self.teacher_client.post(
            reverse("booking:complete_lesson", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(complete_resp.status_code, 400)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RESERVED)

        # Once teacher declines ("Keep") the cancellation request, teacher can complete
        decline_resp = self.teacher_client.post(
            reverse("booking:respond_to_booking", kwargs={"booking_id": booking.id}),
            {"action": "decline"},
        )
        self.assertEqual(decline_resp.status_code, 200)

        complete_after_resp = self.teacher_client.post(
            reverse("booking:complete_lesson", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(complete_after_resp.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.COMPLETED)
        self.assertFalse(booking.is_awaiting_resolution)

    def test_unanswered_cancellation_marked_not_held_teacher_fault_releases_hold(self):
        booking = self._create_unanswered_cancellation_at_start(minutes_since_start=5)
        self.assertTrue(booking.is_awaiting_resolution)

        resp = self.teacher_client.post(
            reverse("booking:mark_lesson_not_held", kwargs={"booking_id": booking.id}),
            {"fault_party": "teacher"},
        )
        self.assertEqual(resp.status_code, 200)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertFalse(booking.cancellation_requested)
        self.assertFalse(booking.is_awaiting_resolution)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            booking.settlement_reason, Booking.SettlementReason.NOT_HELD_TEACHER_FAULT
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_is_awaiting_resolution_false_for_terminal_states_even_if_flag_set(self):
        for terminal_status in (
            Booking.Status.CANCELLED,
            Booking.Status.COMPLETED,
            Booking.Status.EXPIRED,
        ):
            booking = Booking.objects.create(
                student=self.student_user,
                teacher=self.teacher,
                start_at=timezone.now() - timedelta(hours=2),
                end_at=timezone.now() - timedelta(hours=1),
                status=terminal_status,
                cancellation_requested=True,
            )
            self.assertFalse(booking.is_awaiting_resolution)
