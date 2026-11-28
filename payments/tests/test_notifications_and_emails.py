from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Tenant, User
from payments.models import PaymentAddress, Payout
from payments.services import (
    create_payout_request,
    create_topup_order,
    mark_payout_processing,
    process_manual_topup,
    release_payout,
    settle_payout,
    submit_topup_claim,
    update_receiving_address,
    verify_and_reserve_payout,
)
from payments.tasks import payout_reminder_task
from wallet.services import credit_wallet, get_or_create_wallet


class _SimulatedRollback(Exception):
    pass


@override_settings(SITE_URL="https://tutor.example.com", SITE_NAME="English with Mary")
class PayoutEmailAndDedupTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.teacher = User.objects.create_user(
            username="teacher_notif_payout",
            email="teacher_payout@example.com",
            password="password123",
            role=User.Role.TEACHER,
            first_name="Mary",
            is_email_verified=True,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)
        self.student = User.objects.create_user(
            username="student_notif_payout",
            email="student_payout@example.com",
            password="password123",
            role=User.Role.STUDENT,
            first_name="Alice",
            is_email_verified=True,
        )
        self.wallet = get_or_create_wallet(
            tenant=self.tenant,
            user=self.student,
            currency="USD",
        )
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="notif-payout-seed-100",
            reference_id="seed-100",
            description="Seed funds",
        )

    def _create_confirmed_payout(self, amount: Decimal = Decimal("25.00")) -> Payout:
        with self.captureOnCommitCallbacks(execute=True):
            payout = create_payout_request(
                tenant=self.tenant,
                student=self.student,
                amount_gross=amount,
                destination="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
            )
        verify_and_reserve_payout(
            tenant=self.tenant,
            payout=payout,
            raw_token=payout.raw_token,
        )
        mail.outbox.clear()
        return payout

    def test_create_payout_request_rollback_does_not_dispatch_email(self):
        mail.outbox.clear()
        with self.assertRaises(_SimulatedRollback):
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    create_payout_request(
                        tenant=self.tenant,
                        student=self.student,
                        amount_gross=Decimal("20.00"),
                        destination="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
                    )
                    raise _SimulatedRollback("boom")

        self.assertEqual(len(mail.outbox), 0)

    def test_payout_confirmation_email_is_deduplicated_and_recovers_after_failure(self):
        from payments.emails import send_payout_confirmation_email

        with self.captureOnCommitCallbacks(execute=True):
            payout = create_payout_request(
                tenant=self.tenant,
                student=self.student,
                amount_gross=Decimal("20.00"),
                destination="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
            )
        self.assertEqual(len(mail.outbox), 1)

        # Second dispatch with same payout is suppressed by dedup key
        dispatched = send_payout_confirmation_email(payout, payout.raw_token)
        self.assertFalse(dispatched)
        self.assertEqual(len(mail.outbox), 1)

        # If send fails, dedup key is cleared so retry succeeds
        cache.clear()
        mail.outbox.clear()
        with patch(
            "django.core.mail.EmailMultiAlternatives.send",
            side_effect=RuntimeError("SMTP temporary down"),
        ):
            with self.assertRaises(RuntimeError):
                send_payout_confirmation_email(payout, payout.raw_token)

        self.assertTrue(send_payout_confirmation_email(payout, payout.raw_token))
        self.assertEqual(len(mail.outbox), 1)

    def test_payout_status_updates_render_branded_html_and_escape_user_input(self):
        payout = self._create_confirmed_payout()

        with self.captureOnCommitCallbacks(execute=True):
            mark_payout_processing(
                tenant=self.tenant,
                payout=payout,
                admin_user=self.teacher,
            )
        self.assertEqual(len(mail.outbox), 1)
        proc_email = mail.outbox[0]
        self.assertEqual(len(proc_email.alternatives), 1)
        proc_html = proc_email.alternatives[0][0]
        self.assertIn("English with Mary", proc_html)
        self.assertIn("Processing", proc_html)
        self.assertIn("$25.00", proc_html)
        self.assertIn(payout.destination, proc_html)

        mail.outbox.clear()
        txhash = "0xdeadbeef1234567890abcdef1234567890abcdef1234567890abcdef12345678"
        with self.captureOnCommitCallbacks(execute=True):
            settle_payout(
                tenant=self.tenant,
                payout=payout,
                external_ref=txhash,
                admin_user=self.teacher,
            )
        self.assertEqual(len(mail.outbox), 1)
        comp_email = mail.outbox[0]
        self.assertEqual(len(comp_email.alternatives), 1)
        comp_html = comp_email.alternatives[0][0]
        self.assertIn("Completed", comp_html)
        self.assertIn(txhash, comp_html)
        self.assertIn("$25.00", comp_html)

        # Rejected payout escapes HTML in teacher reason
        payout2 = self._create_confirmed_payout(Decimal("15.00"))
        xss_reason = '<script>alert("xss")</script> Invalid BEP-20 memo'
        with self.captureOnCommitCallbacks(execute=True):
            release_payout(
                tenant=self.tenant,
                payout=payout2,
                reason=xss_reason,
                admin_user=self.teacher,
            )
        self.assertEqual(len(mail.outbox), 1)
        rej_email = mail.outbox[0]
        self.assertEqual(len(rej_email.alternatives), 1)
        rej_html = rej_email.alternatives[0][0]
        self.assertIn("Rejected", rej_html)
        self.assertNotIn("<script>", rej_html)
        self.assertIn("&lt;script&gt;", rej_html)

    def test_payout_status_rollback_and_deduplication(self):
        payout = self._create_confirmed_payout()

        with self.assertRaises(_SimulatedRollback):
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    mark_payout_processing(
                        tenant=self.tenant,
                        payout=payout,
                        admin_user=self.teacher,
                    )
                    raise _SimulatedRollback("rollback processing")
        self.assertEqual(len(mail.outbox), 0)

        from payments.emails import send_payout_status_email

        self.assertTrue(
            send_payout_status_email(payout, status=Payout.Status.PROCESSING)
        )
        self.assertFalse(
            send_payout_status_email(payout, status=Payout.Status.PROCESSING)
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_payout_48h_reminder_renders_branded_html_and_deduplicates(self):
        payout = self._create_confirmed_payout()
        payout.confirmation_used_at = timezone.now() - timedelta(hours=50)
        payout.save(update_fields=["confirmation_used_at"])

        with self.captureOnCommitCallbacks(execute=True):
            reminded = payout_reminder_task()
        self.assertEqual(reminded, 1)
        self.assertEqual(len(mail.outbox), 1)
        reminder_email = mail.outbox[0]
        self.assertEqual(reminder_email.to, [self.teacher.email])
        self.assertEqual(len(reminder_email.alternatives), 1)
        reminder_html = reminder_email.alternatives[0][0]
        self.assertIn("48", reminder_html)
        self.assertIn(f"#{payout.id}", reminder_html)
        self.assertIn("$25.00", reminder_html)

        # Direct email call with same dedup key is suppressed
        from payments.emails import send_payout_reminder_48h_email

        self.assertFalse(send_payout_reminder_48h_email(payout))
        self.assertEqual(len(mail.outbox), 1)


@override_settings(SITE_URL="https://tutor.example.com", SITE_NAME="English with Mary")
class TopUpEmailAndClaimLinkTests(TestCase):
    VALID_ADDRESS = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"

    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.teacher = User.objects.create_user(
            username="teacher_notif_topup",
            email="teacher_topup@example.com",
            password="password123",
            role=User.Role.TEACHER,
            first_name="Mary",
            is_email_verified=True,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)
        self.student = User.objects.create_user(
            username="student_notif_topup",
            email="student_topup@example.com",
            password="password123",
            role=User.Role.STUDENT,
            first_name="Bob",
            is_email_verified=True,
        )
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.VALID_ADDRESS,
            status=PaymentAddress.Status.ACTIVE,
        )

    def test_manual_topup_sends_branded_credited_email_with_balance_and_escapes_notes(
        self,
    ):
        xss_notes = "<img src=x onerror=alert(1)> Verified BEP-20 transfer"
        with self.captureOnCommitCallbacks(execute=True):
            payment = process_manual_topup(
                tenant=self.tenant,
                student=self.student,
                amount=Decimal("45.00"),
                reference_id="TX_CREDIT_1001",
                admin_user=self.teacher,
                notes=xss_notes,
            )

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.student.email])
        self.assertEqual(len(sent.alternatives), 1)
        html = sent.alternatives[0][0]
        self.assertIn("English with Mary", html)
        self.assertIn("$45.00", html)
        self.assertIn("TX_CREDIT_1001", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)
        self.assertIn("$45.00", sent.body)
        self.assertIn("TX_CREDIT_1001", sent.body)

        # Duplicate call is suppressed
        from payments.emails import send_topup_credited_email

        self.assertFalse(send_topup_credited_email(payment))
        self.assertEqual(len(mail.outbox), 1)

    def test_manual_topup_rollback_does_not_dispatch_credited_email(self):
        with self.assertRaises(_SimulatedRollback):
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    process_manual_topup(
                        tenant=self.tenant,
                        student=self.student,
                        amount=Decimal("30.00"),
                        reference_id="TX_ROLLBACK_1",
                        admin_user=self.teacher,
                    )
                    raise _SimulatedRollback("rollback topup")

        self.assertEqual(len(mail.outbox), 0)

    def test_topup_claim_alert_links_with_claim_id_query_param(self):
        order = create_topup_order(
            tenant=self.tenant,
            student=self.student,
            amount=Decimal("50.00"),
        )
        with self.captureOnCommitCallbacks(execute=True):
            submit_topup_claim(
                tenant=self.tenant,
                student=self.student,
                order=order,
                txhash="0xclaimhash777",
            )

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        expected_link = (
            f"https://tutor.example.com/dashboard/finance/?claim_id={order.id}"
        )
        self.assertIn(expected_link, sent.body)
        self.assertIn(expected_link, sent.alternatives[0][0])

    def test_receiving_address_change_security_alert_renders_html_and_cooldown(self):
        new_addr = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"
        with self.captureOnCommitCallbacks(execute=True):
            update_receiving_address(
                tenant=self.tenant,
                user=self.teacher,
                raw_address=new_addr,
                current_password="password123",
            )

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.teacher.email])
        self.assertEqual(len(sent.alternatives), 1)
        html = sent.alternatives[0][0]
        self.assertIn("Security Alert", html)
        self.assertIn(new_addr, html)
        self.assertIn("24-hour", html)
        self.assertIn(new_addr, sent.body)
