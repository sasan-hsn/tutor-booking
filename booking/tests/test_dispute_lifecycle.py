from datetime import time, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Tenant, User
from booking.models import Booking, RegularAvailability
from booking.services import (
    DisputeConflictError,
    create_booking_with_hold,
    open_dispute,
    resolve_dispute,
)
from booking.tasks import (
    auto_complete_lessons_task,
    dispute_warning_task,
    expire_disputes_task,
)
from notifications.models import Notification
from wallet.models import LedgerEntry
from wallet.services import credit_wallet, get_or_create_wallet


class DisputeLifecycleBaseTestCase(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()

        self.teacher_user = User.objects.create_user(
            username="teacher_dispute",
            email="teacher_dispute@example.com",
            first_name="Mary",
            last_name="Teacher",
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
            username="student_dispute",
            email="student_dispute@example.com",
            first_name="Alice",
            last_name="Student",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.other_student = User.objects.create_user(
            username="other_student_dispute",
            email="other_student_dispute@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

        self.wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="dispute-seed-100",
            reference_id="dispute_seed",
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
        self.future_slot = timezone.datetime.combine(
            future_day, time(10, 0), tzinfo=self.tz
        )

    def _create_booking(
        self,
        *,
        status=Booking.Status.CONFIRMED,
        end_at=None,
        cancellation_requested=False,
    ):
        booking = create_booking_with_hold(
            teacher=self.teacher,
            student=self.student_user,
            start_at=self.future_slot,
            lesson_option="regular_50",
        )
        if end_at is None:
            end_at = timezone.now() - timedelta(hours=1)
        booking.status = status
        booking.cancellation_requested = cancellation_requested
        booking.end_at = end_at
        booking.start_at = end_at - timedelta(minutes=50)
        booking.save()
        return booking

    def _patch_flaky_settle(self, fail_booking_id: int):
        from wallet.services import settle_booking as real_settle_booking

        def flaky_settle(*, tenant, booking, action, actor, reason):
            if booking.id == fail_booking_id:
                raise RuntimeError("Simulated settlement error")
            return real_settle_booking(
                tenant=tenant,
                booking=booking,
                action=action,
                actor=actor,
                reason=reason,
            )

        return patch("booking.tasks.settle_booking", side_effect=flaky_settle)


class OpenDisputeServiceTests(DisputeLifecycleBaseTestCase):
    def test_open_dispute_at_end_at_plus_1h_while_confirmed_succeeds(self):
        end_at = timezone.now() - timedelta(hours=1)
        booking = self._create_booking(status=Booking.Status.CONFIRMED, end_at=end_at)
        dispute_now = end_at + timedelta(hours=1)

        updated = open_dispute(
            booking=booking, student=self.student_user, now=dispute_now
        )

        updated.refresh_from_db()
        self.assertEqual(updated.status, Booking.Status.DISPUTING)
        self.assertEqual(updated.dispute_opened_at, dispute_now)
        self.assertEqual(updated.payment_status, Booking.PaymentStatus.RESERVED)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("25.00"))
        self.assertFalse(
            LedgerEntry.objects.filter(
                idempotency_key=f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
            ).exists()
        )

    def test_open_dispute_at_exact_48h_boundary_succeeds(self):
        end_at = timezone.now() - timedelta(hours=48)
        booking = self._create_booking(status=Booking.Status.CONFIRMED, end_at=end_at)
        dispute_now = end_at + timedelta(hours=settings.DISPUTE_WINDOW_HOURS)

        updated = open_dispute(
            booking=booking, student=self.student_user, now=dispute_now
        )

        updated.refresh_from_db()
        self.assertEqual(updated.status, Booking.Status.DISPUTING)
        self.assertEqual(updated.dispute_opened_at, dispute_now)

    def test_open_dispute_before_end_at_fails_with_validation_error(self):
        end_at = timezone.now() + timedelta(minutes=30)
        booking = self._create_booking(status=Booking.Status.CONFIRMED, end_at=end_at)

        with self.assertRaises(ValidationError):
            open_dispute(
                booking=booking,
                student=self.student_user,
                now=end_at - timedelta(minutes=1),
            )

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertIsNone(booking.dispute_opened_at)

    def test_open_dispute_at_end_at_plus_49h_fails_with_validation_error(self):
        end_at = timezone.now() - timedelta(hours=50)
        booking = self._create_booking(status=Booking.Status.CONFIRMED, end_at=end_at)

        with self.assertRaises(ValidationError):
            open_dispute(
                booking=booking,
                student=self.student_user,
                now=end_at + timedelta(hours=49),
            )

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertIsNone(booking.dispute_opened_at)

    def test_open_dispute_after_teacher_complete_lesson_fails_with_final_message(self):
        end_at = timezone.now() - timedelta(hours=2)
        booking = self._create_booking(status=Booking.Status.CONFIRMED, end_at=end_at)

        teacher_client = self.client_class()
        teacher_client.force_login(self.teacher_user)
        resp = teacher_client.post(
            reverse("booking:complete_lesson", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(resp.status_code, 200)

        booking.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "Lesson completion is final"):
            open_dispute(
                booking=booking,
                student=self.student_user,
                now=end_at + timedelta(hours=3),
            )

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.COMPLETED)
        self.assertIsNone(booking.dispute_opened_at)

    def test_open_dispute_by_non_enrolled_student_fails(self):
        end_at = timezone.now() - timedelta(hours=1)
        booking = self._create_booking(status=Booking.Status.CONFIRMED, end_at=end_at)

        with self.assertRaises(ValidationError):
            open_dispute(
                booking=booking,
                student=self.other_student,
                now=end_at + timedelta(minutes=30),
            )

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_open_dispute_on_already_disputing_or_pending_booking_fails(self):
        base_end_at = timezone.now() - timedelta(hours=1)
        for idx, bad_status in enumerate(
            (
                Booking.Status.PENDING,
                Booking.Status.CANCELLED,
                Booking.Status.DISPUTING,
                Booking.Status.EXPIRED,
            )
        ):
            booking_end = base_end_at - timedelta(hours=idx * 2)
            booking = self._create_booking(status=bad_status, end_at=booking_end)
            with self.assertRaises(ValidationError):
                open_dispute(
                    booking=booking,
                    student=self.student_user,
                    now=booking_end + timedelta(minutes=15),
                )


class OpenDisputeViewTests(DisputeLifecycleBaseTestCase):
    def setUp(self):
        super().setUp()
        self.student_client = self.client_class()
        self.student_client.force_login(self.student_user)
        self.other_student_client = self.client_class()
        self.other_student_client.force_login(self.other_student)
        self.teacher_client = self.client_class()
        self.teacher_client.force_login(self.teacher_user)

    def test_student_can_open_dispute_via_http_within_48h_window(self):
        end_at = timezone.now() - timedelta(hours=2)
        booking = self._create_booking(status=Booking.Status.CONFIRMED, end_at=end_at)
        dispute_url = reverse("booking:open_dispute", kwargs={"booking_id": booking.id})

        detail_resp = self.student_client.get(
            reverse("booking:lesson_detail_student", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, 'id="openDisputeBtn"')
        self.assertContains(detail_resp, f'data-dispute-url="{dispute_url}"')

        resp = self.student_client.post(
            dispute_url,
            {"reason": "Teacher did not join the video call."},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.DISPUTING)
        self.assertIsNotNone(booking.dispute_opened_at)
        self.assertEqual(booking.dispute_reason, "Teacher did not join the video call.")
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.RESERVED)

        # Modal now shows disputing notice instead of openDisputeBtn
        detail_after = self.student_client.get(
            reverse("booking:lesson_detail_student", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(detail_after.status_code, 200)
        self.assertNotContains(detail_after, 'id="openDisputeBtn"')
        self.assertContains(detail_after, "dispute", status_code=200)

    def test_dispute_after_teacher_complete_lesson_rejected_with_400(self):
        end_at = timezone.now() - timedelta(hours=2)
        booking = self._create_booking(status=Booking.Status.CONFIRMED, end_at=end_at)

        complete_resp = self.teacher_client.post(
            reverse("booking:complete_lesson", kwargs={"booking_id": booking.id})
        )
        self.assertEqual(complete_resp.status_code, 200)

        detail_resp = self.student_client.get(
            reverse("booking:lesson_detail_student", kwargs={"booking_id": booking.id})
        )
        self.assertNotContains(detail_resp, 'id="openDisputeBtn"')

        resp = self.student_client.post(
            reverse("booking:open_dispute", kwargs={"booking_id": booking.id}),
            {"reason": "Audio cut out completely."},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Lesson completion is final", resp.json()["error"])

    def test_dispute_outside_48h_window_or_before_end_rejected_with_400(self):
        future_booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=timezone.now() + timedelta(hours=2),
        )
        resp_early = self.student_client.post(
            reverse("booking:open_dispute", kwargs={"booking_id": future_booking.id}),
            {"reason": "Too early"},
        )
        self.assertEqual(resp_early.status_code, 400)

        expired_booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=timezone.now() - timedelta(hours=49),
        )
        detail_expired = self.student_client.get(
            reverse(
                "booking:lesson_detail_student",
                kwargs={"booking_id": expired_booking.id},
            )
        )
        self.assertNotContains(detail_expired, 'id="openDisputeBtn"')

        resp_late = self.student_client.post(
            reverse("booking:open_dispute", kwargs={"booking_id": expired_booking.id}),
            {"reason": "Too late"},
        )
        self.assertEqual(resp_late.status_code, 400)

    def test_open_dispute_requires_non_empty_reason_in_view_and_modal(self):
        booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=timezone.now() - timedelta(hours=2),
        )
        detail_resp = self.student_client.get(
            reverse("booking:lesson_detail_student", kwargs={"booking_id": booking.id})
        )
        self.assertContains(detail_resp, 'id="disputeReasonInput"')

        dispute_url = reverse("booking:open_dispute", kwargs={"booking_id": booking.id})
        resp = self.student_client.post(dispute_url, {"reason": "   "})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reason", resp.json()["error"].lower())
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_dispute_endpoint_access_control(self):
        booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=timezone.now() - timedelta(hours=2),
        )
        dispute_url = reverse("booking:open_dispute", kwargs={"booking_id": booking.id})

        self.assertEqual(self.teacher_client.post(dispute_url).status_code, 403)
        self.assertEqual(self.other_student_client.post(dispute_url).status_code, 404)


class ResolveDisputeServiceTests(DisputeLifecycleBaseTestCase):
    def test_resolve_dispute_student_favor_releases_hold_sets_resolved_and_notifies(
        self,
    ):
        end_at = timezone.now() - timedelta(hours=3)
        booking = self._create_booking(status=Booking.Status.CONFIRMED, end_at=end_at)
        open_dispute(
            booking=booking,
            student=self.student_user,
            reason="Connection dropped 5 minutes into class.",
            now=end_at + timedelta(hours=1),
        )
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            resolved = resolve_dispute(
                tenant=self.tenant,
                booking=booking,
                teacher_user=self.teacher_user,
                resolution=Booking.DisputeResolution.STUDENT_FAVOR,
            )

        resolved.refresh_from_db()
        self.assertEqual(resolved.status, Booking.Status.RESOLVED)
        self.assertEqual(resolved.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            resolved.settlement_reason, Booking.SettlementReason.DISPUTE_STUDENT_FAVOR
        )
        self.assertEqual(
            resolved.dispute_resolution, Booking.DisputeResolution.STUDENT_FAVOR
        )
        self.assertEqual(resolved.dispute_resolved_by, self.teacher_user)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("100.00"))

        settle_entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
        )
        self.assertEqual(settle_entry.entry_type, LedgerEntry.EntryType.RELEASE)

        self.assertEqual(len(mail.outbox), 2)
        recipients = {tuple(m.to) for m in mail.outbox}
        self.assertEqual(
            recipients,
            {(self.student_user.email,), (self.teacher_user.email,)},
        )
        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.DISPUTE_RESOLVED,
                metadata__booking_id=booking.id,
            ).count(),
            2,
        )

    def test_resolve_dispute_teacher_favor_consumes_hold_sets_completed_and_notifies(
        self,
    ):
        end_at = timezone.now() - timedelta(hours=3)
        booking = self._create_booking(status=Booking.Status.CONFIRMED, end_at=end_at)
        open_dispute(
            booking=booking,
            student=self.student_user,
            reason="Disagreed on lesson duration.",
            now=end_at + timedelta(hours=1),
        )
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            resolved = resolve_dispute(
                tenant=self.tenant,
                booking=booking,
                teacher_user=self.teacher_user,
                resolution=Booking.DisputeResolution.TEACHER_FAVOR,
            )

        resolved.refresh_from_db()
        self.assertEqual(resolved.status, Booking.Status.COMPLETED)
        self.assertEqual(resolved.payment_status, Booking.PaymentStatus.CONSUMED)
        self.assertEqual(
            resolved.settlement_reason, Booking.SettlementReason.DISPUTE_TEACHER_FAVOR
        )
        self.assertEqual(
            resolved.dispute_resolution, Booking.DisputeResolution.TEACHER_FAVOR
        )
        self.assertEqual(resolved.dispute_resolved_by, self.teacher_user)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("75.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("75.00"))

        settle_entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:booking:{booking.id}:settle"
        )
        self.assertEqual(settle_entry.entry_type, LedgerEntry.EntryType.CONSUME)

        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.DISPUTE_RESOLVED,
                metadata__booking_id=booking.id,
            ).count(),
            2,
        )

    def test_resolve_dispute_validation_and_conflict_guards(self):
        end_at = timezone.now() - timedelta(hours=2)
        confirmed_booking = self._create_booking(
            status=Booking.Status.CONFIRMED, end_at=end_at
        )

        with self.assertRaises(ValidationError):
            resolve_dispute(
                tenant=self.tenant,
                booking=confirmed_booking,
                teacher_user=self.teacher_user,
                resolution="EXPIRED_AUTO_STUDENT",
            )

        with self.assertRaises(DisputeConflictError):
            resolve_dispute(
                tenant=self.tenant,
                booking=confirmed_booking,
                teacher_user=self.teacher_user,
                resolution=Booking.DisputeResolution.STUDENT_FAVOR,
            )

        open_dispute(
            booking=confirmed_booking,
            student=self.student_user,
            reason="Test issue",
            now=end_at + timedelta(minutes=30),
        )
        with self.assertRaises(ValidationError):
            resolve_dispute(
                tenant=self.tenant,
                booking=confirmed_booking,
                teacher_user=self.student_user,
                resolution=Booking.DisputeResolution.STUDENT_FAVOR,
            )


class AutoCompleteLessonsTaskTests(DisputeLifecycleBaseTestCase):
    def test_settings_and_beat_schedule_registered(self):
        self.assertEqual(getattr(settings, "AUTO_COMPLETE_HOURS", None), 48)
        schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
        self.assertIn("auto-complete-lessons-hourly", schedule)
        self.assertEqual(
            schedule["auto-complete-lessons-hourly"]["task"],
            "booking.tasks.auto_complete_lessons_task",
        )
        self.assertEqual(
            schedule["auto-complete-lessons-hourly"]["schedule"],
            3600.0,
        )

    def test_auto_complete_sweeps_confirmed_48h_past_and_skips_disputing_and_recent(
        self,
    ):
        now = timezone.now()
        due_booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(hours=48),
        )
        disputing_booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(hours=50),
        )
        open_dispute(
            booking=disputing_booking,
            student=self.student_user,
            now=disputing_booking.end_at + timedelta(hours=1),
        )
        recent_booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(hours=47),
        )
        free_booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now - timedelta(hours=53),
            end_at=now - timedelta(hours=52),
            lesson_type=Booking.LessonType.TRIAL,
            price=Decimal("0.00"),
            status=Booking.Status.CONFIRMED,
            payment_status=Booking.PaymentStatus.NOT_REQUIRED,
        )

        completed_count = auto_complete_lessons_task(now=now)

        self.assertEqual(completed_count, 1)

        due_booking.refresh_from_db()
        self.assertEqual(due_booking.status, Booking.Status.COMPLETED)
        self.assertEqual(due_booking.payment_status, Booking.PaymentStatus.CONSUMED)
        self.assertEqual(
            due_booking.settlement_reason, Booking.SettlementReason.COMPLETED
        )

        disputing_booking.refresh_from_db()
        self.assertEqual(disputing_booking.status, Booking.Status.DISPUTING)
        self.assertEqual(
            disputing_booking.payment_status, Booking.PaymentStatus.RESERVED
        )

        recent_booking.refresh_from_db()
        self.assertEqual(recent_booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(recent_booking.payment_status, Booking.PaymentStatus.RESERVED)

        free_booking.refresh_from_db()
        self.assertEqual(free_booking.status, Booking.Status.CONFIRMED)

        # Wallet: 100 initial, 3 reserved bookings * 25 = 75 reserved, 1 consumed (due_booking)
        # => settled = 75.00, reserved = 50.00 (disputing + recent)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("75.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("50.00"))

        due_entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:booking:{due_booking.id}:settle"
        )
        self.assertEqual(due_entry.entry_type, LedgerEntry.EntryType.CONSUME)

    def test_auto_complete_isolates_per_booking_transaction(self):
        now = timezone.now()
        booking_fail = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(hours=50),
        )
        booking_ok = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(hours=49),
        )

        with (
            self.assertLogs("booking.tasks", level="ERROR"),
            self._patch_flaky_settle(booking_fail.id),
        ):
            completed_count = auto_complete_lessons_task(now=now)

        self.assertEqual(completed_count, 1)
        booking_fail.refresh_from_db()
        self.assertEqual(booking_fail.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking_fail.payment_status, Booking.PaymentStatus.RESERVED)

        booking_ok.refresh_from_db()
        self.assertEqual(booking_ok.status, Booking.Status.COMPLETED)
        self.assertEqual(booking_ok.payment_status, Booking.PaymentStatus.CONSUMED)

    def test_auto_complete_and_open_dispute_skip_pending_cancellation_request(self):
        now = timezone.now()
        pending_cancel_booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(hours=50),
            cancellation_requested=True,
        )

        completed_count = auto_complete_lessons_task(now=now)
        self.assertEqual(completed_count, 0)
        pending_cancel_booking.refresh_from_db()
        self.assertEqual(pending_cancel_booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(
            pending_cancel_booking.payment_status, Booking.PaymentStatus.RESERVED
        )

        with self.assertRaisesMessage(ValidationError, "pending cancellation request"):
            open_dispute(
                booking=pending_cancel_booking,
                student=self.student_user,
                now=pending_cancel_booking.end_at + timedelta(hours=1),
            )


class ExpireDisputesTaskTests(DisputeLifecycleBaseTestCase):
    def test_settings_and_beat_schedule_registered(self):
        self.assertEqual(getattr(settings, "DISPUTE_WINDOW_DAYS", None), 7)
        schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
        self.assertIn("expire-disputes-hourly", schedule)
        self.assertEqual(
            schedule["expire-disputes-hourly"]["task"],
            "booking.tasks.expire_disputes_task",
        )
        self.assertEqual(
            schedule["expire-disputes-hourly"]["schedule"],
            3600.0,
        )

    def test_expire_disputes_releases_funds_after_7_days_and_skips_recent(self):
        now = timezone.now()

        expired_dispute = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(days=8),
        )
        open_dispute(
            booking=expired_dispute,
            student=self.student_user,
            now=now - timedelta(days=7),
        )

        active_dispute = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(days=7),
        )
        open_dispute(
            booking=active_dispute,
            student=self.student_user,
            now=now - timedelta(days=6, hours=23),
        )

        unpaid_dispute = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now - timedelta(days=9, hours=1),
            end_at=now - timedelta(days=9),
            lesson_type=Booking.LessonType.TRIAL,
            price=Decimal("0.00"),
            status=Booking.Status.DISPUTING,
            payment_status=Booking.PaymentStatus.NOT_REQUIRED,
            dispute_opened_at=now - timedelta(days=8),
        )

        expired_count = expire_disputes_task(now=now)

        self.assertEqual(expired_count, 1)

        expired_dispute.refresh_from_db()
        self.assertEqual(expired_dispute.status, Booking.Status.CANCELLED)
        self.assertEqual(expired_dispute.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(
            expired_dispute.settlement_reason,
            Booking.SettlementReason.DISPUTE_EXPIRED,
        )
        self.assertEqual(expired_dispute.dispute_resolution, "EXPIRED_AUTO_STUDENT")

        active_dispute.refresh_from_db()
        self.assertEqual(active_dispute.status, Booking.Status.DISPUTING)
        self.assertEqual(active_dispute.payment_status, Booking.PaymentStatus.RESERVED)
        self.assertIsNone(active_dispute.dispute_resolution)

        unpaid_dispute.refresh_from_db()
        self.assertEqual(unpaid_dispute.status, Booking.Status.DISPUTING)

        # Wallet: 100 settled, only active_dispute ($25) still reserved
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("25.00"))

        release_entry = LedgerEntry.objects.get(
            idempotency_key=f"tenant:{self.tenant.id}:booking:{expired_dispute.id}:settle"
        )
        self.assertEqual(release_entry.entry_type, LedgerEntry.EntryType.RELEASE)

    def test_expire_disputes_isolates_per_booking_transaction(self):
        now = timezone.now()
        dispute_fail = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(days=9),
        )
        open_dispute(
            booking=dispute_fail,
            student=self.student_user,
            now=now - timedelta(days=8),
        )
        dispute_ok = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(days=8),
        )
        open_dispute(
            booking=dispute_ok,
            student=self.student_user,
            now=now - timedelta(days=7, hours=1),
        )

        with (
            self.assertLogs("booking.tasks", level="ERROR"),
            self._patch_flaky_settle(dispute_fail.id),
        ):
            expired_count = expire_disputes_task(now=now)

        self.assertEqual(expired_count, 1)
        dispute_fail.refresh_from_db()
        self.assertEqual(dispute_fail.status, Booking.Status.DISPUTING)
        self.assertEqual(dispute_fail.payment_status, Booking.PaymentStatus.RESERVED)

        dispute_ok.refresh_from_db()
        self.assertEqual(dispute_ok.status, Booking.Status.CANCELLED)
        self.assertEqual(dispute_ok.payment_status, Booking.PaymentStatus.RELEASED)
        self.assertEqual(dispute_ok.dispute_resolution, "EXPIRED_AUTO_STUDENT")


class DisputeWarningTaskTests(DisputeLifecycleBaseTestCase):
    def test_beat_schedule_registered(self):
        schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
        self.assertIn("dispute-warning-daily", schedule)
        self.assertEqual(
            schedule["dispute-warning-daily"]["task"],
            "booking.tasks.dispute_warning_task",
        )
        self.assertEqual(
            schedule["dispute-warning-daily"]["schedule"],
            86400.0,
        )

    def test_dispute_warning_sends_day3_and_day6_alerts_and_is_idempotent(self):
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="dispute-warning-extra-50",
            reference_id="dispute_warning_seed",
            description="Extra $50.00 for 5 bookings",
        )
        now = timezone.now()

        day3_booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(days=4),
        )
        open_dispute(
            booking=day3_booking,
            student=self.student_user,
            now=now - timedelta(days=3, hours=2),
        )

        day6_booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(days=7),
        )
        open_dispute(
            booking=day6_booking,
            student=self.student_user,
            now=now - timedelta(days=6, hours=5),
        )

        # Should be skipped: Day 2, Day 4, Day 7, and non-DISPUTING
        day2_booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(days=3),
        )
        open_dispute(
            booking=day2_booking,
            student=self.student_user,
            now=now - timedelta(days=2, hours=12),
        )

        day4_booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(days=5),
        )
        open_dispute(
            booking=day4_booking,
            student=self.student_user,
            now=now - timedelta(days=4, hours=1),
        )

        day7_booking = self._create_booking(
            status=Booking.Status.CONFIRMED,
            end_at=now - timedelta(days=8),
        )
        open_dispute(
            booking=day7_booking,
            student=self.student_user,
            now=now - timedelta(days=7),
        )

        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now - timedelta(days=10, hours=1),
            end_at=now - timedelta(days=10),
            lesson_type=Booking.LessonType.REGULAR,
            price=Decimal("25.00"),
            status=Booking.Status.COMPLETED,
            dispute_opened_at=now - timedelta(days=3, hours=1),
        )
        Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=now - timedelta(days=11, hours=1),
            end_at=now - timedelta(days=11),
            lesson_type=Booking.LessonType.TRIAL,
            price=Decimal("0.00"),
            status=Booking.Status.DISPUTING,
            payment_status=Booking.PaymentStatus.NOT_REQUIRED,
            dispute_opened_at=now - timedelta(days=3, hours=1),
        )

        mail.outbox.clear()
        sent_count = dispute_warning_task(now=now)

        self.assertEqual(sent_count, 2)
        self.assertEqual(len(mail.outbox), 2)

        recipients = [m.to for m in mail.outbox]
        self.assertEqual(
            recipients,
            [[self.teacher_user.email], [self.teacher_user.email]],
        )

        bodies_by_subject = {m.subject: m.body for m in mail.outbox}
        subjects_joined = "\n".join(bodies_by_subject.keys())
        self.assertIn(f"#{day3_booking.id}", subjects_joined)
        self.assertIn(f"#{day6_booking.id}", subjects_joined)

        day3_email = next(m for m in mail.outbox if f"#{day3_booking.id}" in m.subject)
        day6_email = next(m for m in mail.outbox if f"#{day6_booking.id}" in m.subject)
        self.assertIn("4 days", day3_email.body)
        self.assertIn("1 day", day6_email.body)

        # Second invocation on same day must be idempotent
        second_count = dispute_warning_task(now=now + timedelta(hours=1))
        self.assertEqual(second_count, 0)
        self.assertEqual(len(mail.outbox), 2)
