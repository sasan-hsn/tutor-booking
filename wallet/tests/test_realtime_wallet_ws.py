from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import transaction
from django.test import TransactionTestCase
from django.utils import timezone

from accounts.models import Tenant
from booking.models import Booking
from booking.services import open_dispute
from payments.models import PaymentAddress
from payments.services import create_topup_order, submit_topup_claim
from tutor_booking.asgi import application
from wallet.models import LedgerEntry
from wallet.services import (
    adjust_wallet,
    credit_wallet,
    get_or_create_wallet,
    waive_penalty,
)

User = get_user_model()


class _SimulatedRollback(Exception):
    pass


@database_sync_to_async
def _create_session(user):
    session = SessionStore()
    session["_auth_user_id"] = str(user.pk)
    session["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    session["_auth_user_hash"] = user.get_session_auth_hash()
    session.save()
    return session.session_key


async def _connect_user(user):
    session_key = await _create_session(user)
    headers = [
        (b"cookie", f"{settings.SESSION_COOKIE_NAME}={session_key}".encode("ascii"))
    ]
    communicator = WebsocketCommunicator(
        application, "/ws/notifications/", headers=headers
    )
    connected, _ = await communicator.connect()
    return communicator, connected


class RealtimeFinancialWebSocketTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="ws_fin_teacher",
            email="ws_fin_teacher@example.com",
            password="password123",
            role=User.Role.TEACHER,
            first_name="Mary",
            is_email_verified=True,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)
        self.student = User.objects.create_user(
            username="ws_fin_student",
            email="ws_fin_student@example.com",
            password="password123",
            role=User.Role.STUDENT,
            first_name="Alice",
            is_email_verified=True,
        )
        self.other_student = User.objects.create_user(
            username="ws_fin_other_student",
            email="ws_fin_other@example.com",
            password="password123",
            role=User.Role.STUDENT,
            first_name="Eve",
            is_email_verified=True,
        )
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
            status=PaymentAddress.Status.ACTIVE,
        )

    async def test_unauthenticated_cannot_connect_to_financial_channel(self):
        communicator = WebsocketCommunicator(application, "/ws/notifications/")
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
        await communicator.disconnect()

    async def test_credit_wallet_pushes_live_balance_update_only_to_owning_student(
        self,
    ):
        student_ws, student_ok = await _connect_user(self.student)
        other_ws, other_ok = await _connect_user(self.other_student)
        self.assertTrue(student_ok)
        self.assertTrue(other_ok)

        @database_sync_to_async
        def do_credit():
            wallet = get_or_create_wallet(
                tenant=self.tenant, user=self.student, currency="USD"
            )
            return credit_wallet(
                tenant=self.tenant,
                wallet=wallet,
                amount=Decimal("40.00"),
                idempotency_key="ws-credit-40",
                reference_id="ref-ws-40",
                description="Manual top-up",
            )

        await do_credit()

        frame = await student_ws.receive_json_from()
        self.assertEqual(frame["type"], "wallet_balance_updated")
        self.assertEqual(frame["entry_type"], LedgerEntry.EntryType.CREDIT)
        self.assertEqual(frame["amount"], "40.00")
        self.assertEqual(frame["available_balance"], "40.00")
        self.assertEqual(frame["settled_balance"], "40.00")
        self.assertEqual(frame["reserved_balance"], "0.00")
        self.assertEqual(frame["title"], "Deposit Credited")

        # Unauthorized other student receives nothing
        self.assertTrue(await other_ws.receive_nothing())

        await student_ws.disconnect()
        await other_ws.disconnect()

    async def test_credit_wallet_contextual_toast_differentiates_waiver_and_adjustment(
        self,
    ):
        student_ws, connected = await _connect_user(self.student)
        self.assertTrue(connected)

        @database_sync_to_async
        def do_waiver_and_adjustment():
            wallet = get_or_create_wallet(
                tenant=self.tenant, user=self.student, currency="USD"
            )
            credit_wallet(
                tenant=self.tenant,
                wallet=wallet,
                amount=Decimal("25.00"),
                idempotency_key="ws-waiver-25",
                reference_id="booking_99",
                description="Penalty waiver",
                entry_type=LedgerEntry.EntryType.WAIVER,
            )
            credit_wallet(
                tenant=self.tenant,
                wallet=wallet,
                amount=Decimal("10.00"),
                idempotency_key="ws-adj-10",
                reference_id="adj_1",
                description="Admin correction",
                entry_type=LedgerEntry.EntryType.ADJUSTMENT_CREDIT,
            )

        await do_waiver_and_adjustment()

        waiver_frame = await student_ws.receive_json_from()
        self.assertEqual(waiver_frame["type"], "wallet_balance_updated")
        self.assertEqual(waiver_frame["entry_type"], LedgerEntry.EntryType.WAIVER)
        self.assertNotIn("Deposit", waiver_frame["title"])
        self.assertEqual(waiver_frame["available_balance"], "25.00")

        adj_frame = await student_ws.receive_json_from()
        self.assertEqual(adj_frame["type"], "wallet_balance_updated")
        self.assertEqual(
            adj_frame["entry_type"], LedgerEntry.EntryType.ADJUSTMENT_CREDIT
        )
        self.assertNotIn("Deposit", adj_frame["title"])
        self.assertEqual(adj_frame["available_balance"], "35.00")

        await student_ws.disconnect()

    async def test_credit_wallet_rollback_does_not_broadcast_and_channel_error_does_not_abort(
        self,
    ):
        student_ws, connected = await _connect_user(self.student)
        self.assertTrue(connected)

        @database_sync_to_async
        def rollback_credit():
            wallet = get_or_create_wallet(
                tenant=self.tenant, user=self.student, currency="USD"
            )
            try:
                with transaction.atomic():
                    credit_wallet(
                        tenant=self.tenant,
                        wallet=wallet,
                        amount=Decimal("50.00"),
                        idempotency_key="ws-rollback-50",
                        reference_id="ref-rb-50",
                        description="Rolled back",
                    )
                    raise _SimulatedRollback("rollback")
            except _SimulatedRollback:
                pass

        await rollback_credit()
        self.assertTrue(await student_ws.receive_nothing())

        @database_sync_to_async
        def credit_with_channel_failure():
            wallet = get_or_create_wallet(
                tenant=self.tenant, user=self.student, currency="USD"
            )
            channel_layer = get_channel_layer()
            with patch.object(
                channel_layer,
                "group_send",
                side_effect=RuntimeError("Redis unavailable"),
            ):
                credit_wallet(
                    tenant=self.tenant,
                    wallet=wallet,
                    amount=Decimal("15.00"),
                    idempotency_key="ws-channel-err-15",
                    reference_id="ref-err-15",
                    description="Commit survives channel error",
                )
            wallet.refresh_from_db()
            return wallet.available_balance

        available = await credit_with_channel_failure()
        self.assertEqual(available, Decimal("15.00"))
        await student_ws.disconnect()

    async def test_teacher_receives_live_toasts_for_topup_claim_and_open_dispute(self):
        teacher_ws, connected = await _connect_user(self.teacher)
        self.assertTrue(connected)

        @database_sync_to_async
        def trigger_claim_and_dispute():
            order = create_topup_order(
                tenant=self.tenant,
                student=self.student,
                amount=Decimal("60.00"),
            )
            submit_topup_claim(
                tenant=self.tenant,
                student=self.student,
                order=order,
                txhash="0xclaimws999",
            )

            end_at = timezone.now() - timedelta(hours=2)
            booking = Booking.objects.create(
                student=self.student,
                teacher=self.teacher.teacher_profile,
                start_at=end_at - timedelta(minutes=50),
                end_at=end_at,
                lesson_type=Booking.LessonType.REGULAR,
                price=Decimal("25.00"),
                status=Booking.Status.CONFIRMED,
                payment_status=Booking.PaymentStatus.RESERVED,
                held_amount=Decimal("25.00"),
            )
            open_dispute(
                booking=booking,
                student=self.student,
                reason="Audio issue",
                now=end_at + timedelta(hours=1),
            )

        await trigger_claim_and_dispute()

        claim_frame = await teacher_ws.receive_json_from()
        self.assertEqual(claim_frame["type"], "notification_message")
        self.assertEqual(claim_frame["event_type"], "topup_claim_submitted")

        dispute_frame = await teacher_ws.receive_json_from()
        self.assertEqual(dispute_frame["type"], "notification_message")
        self.assertEqual(dispute_frame["event_type"], "dispute_opened")

        await teacher_ws.disconnect()

    async def test_adjust_wallet_credit_and_waive_penalty_push_contextual_balance_updates(
        self,
    ):
        student_ws, connected = await _connect_user(self.student)
        self.assertTrue(connected)

        @database_sync_to_async
        def run_adjustment_and_waiver():
            wallet = get_or_create_wallet(
                tenant=self.tenant, user=self.student, currency="USD"
            )
            adjust_wallet(
                tenant=self.tenant,
                wallet=wallet,
                amount=Decimal("20.00"),
                direction="credit",
                reason="Goodwill adjustment",
                admin_user=self.teacher,
            )
            now = timezone.now()
            consumed_booking = Booking.objects.create(
                student=self.student,
                teacher=self.teacher.teacher_profile,
                start_at=now - timedelta(hours=2),
                end_at=now - timedelta(hours=1),
                lesson_type=Booking.LessonType.REGULAR,
                price=Decimal("25.00"),
                status=Booking.Status.CANCELLED,
                payment_status=Booking.PaymentStatus.CONSUMED,
                held_amount=Decimal("25.00"),
                settlement_reason=Booking.SettlementReason.LATE_CANCEL,
            )
            waive_penalty(
                tenant=self.tenant,
                booking=consumed_booking,
                teacher_user=self.teacher,
                reason="Emergency waiver",
            )

        await run_adjustment_and_waiver()

        adj_frame = await student_ws.receive_json_from()
        self.assertEqual(adj_frame["type"], "wallet_balance_updated")
        self.assertEqual(
            adj_frame["entry_type"], LedgerEntry.EntryType.ADJUSTMENT_CREDIT
        )
        self.assertEqual(adj_frame["title"], "Wallet Balance Adjusted")
        self.assertEqual(adj_frame["available_balance"], "20.00")

        waiver_frame = await student_ws.receive_json_from()
        self.assertEqual(waiver_frame["type"], "wallet_balance_updated")
        self.assertEqual(waiver_frame["entry_type"], LedgerEntry.EntryType.WAIVER)
        self.assertEqual(waiver_frame["title"], "Penalty Waiver Credited")
        self.assertEqual(waiver_frame["available_balance"], "45.00")

        await student_ws.disconnect()
