from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import Tenant, User
from payments.models import PaymentAddress
from payments.services import update_receiving_address, validate_bep20_address


class ValidateBep20AddressTests(TestCase):
    EIP55_VECTORS = [
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
        "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
    ]

    def test_valid_eip55_mixed_case_addresses_accepted(self):
        for addr in self.EIP55_VECTORS:
            self.assertEqual(validate_bep20_address(addr), addr)

    def test_lowercase_and_uppercase_addresses_normalized_to_eip55(self):
        for addr in self.EIP55_VECTORS:
            self.assertEqual(validate_bep20_address(addr.lower()), addr)
            self.assertEqual(
                validate_bep20_address(addr.upper().replace("0X", "0x")), addr
            )

    def test_invalid_mixed_case_checksum_rejected(self):
        # Flip one character's case in a valid mixed-case EIP-55 address
        bad_checksum = "0x5aaeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
        with self.assertRaises(ValidationError):
            validate_bep20_address(bad_checksum)

    def test_invalid_formats_rejected(self):
        invalid_inputs = [
            "",
            "   ",
            "5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",  # missing 0x
            "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAe",  # too short
            "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed1",  # too long
            "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeG",  # non-hex char
        ]
        for raw in invalid_inputs:
            with self.assertRaises(ValidationError):
                validate_bep20_address(raw)


class UpdateReceivingAddressServiceTests(TestCase):
    ADDR_1 = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    ADDR_2 = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"
    ADDR_3 = "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB"

    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username="teacher_addr_svc",
            email="teacher_addr_svc@example.com",
            password="CorrectPassword123!",
            role=User.Role.TEACHER,
            first_name="Mary",
            is_email_verified=True,
        )
        self.tenant = Tenant.objects.get(
            teacher_profile=self.teacher_user.teacher_profile
        )

    def test_first_address_activates_immediately_with_zero_cooldown(self):
        mail.outbox.clear()
        before = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            address_obj = update_receiving_address(
                tenant=self.tenant,
                user=self.teacher_user,
                raw_address=self.ADDR_1.lower(),
                current_password="CorrectPassword123!",
            )
        after = timezone.now()

        self.assertEqual(address_obj.tenant, self.tenant)
        self.assertEqual(address_obj.provider, "crypto_usdt")
        self.assertEqual(address_obj.address, self.ADDR_1)
        self.assertEqual(address_obj.status, PaymentAddress.Status.ACTIVE)
        self.assertIsNotNone(address_obj.activates_at)
        self.assertGreaterEqual(address_obj.activates_at, before)
        self.assertLessEqual(address_obj.activates_at, after)
        self.assertEqual(
            PaymentAddress.objects.filter(tenant=self.tenant).count(),
            1,
        )

    def test_incorrect_password_raises_permission_denied_without_db_mutation(self):
        mail.outbox.clear()
        with self.assertRaises(PermissionDenied):
            update_receiving_address(
                tenant=self.tenant,
                user=self.teacher_user,
                raw_address=self.ADDR_1,
                current_password="WrongPassword!",
            )
        self.assertEqual(PaymentAddress.objects.filter(tenant=self.tenant).count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_subsequent_address_enters_24h_cooldown_and_dispatches_security_alert_on_commit(
        self,
    ):
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_1,
            status=PaymentAddress.Status.ACTIVE,
            activates_at=timezone.now() - timedelta(days=5),
        )

        mail.outbox.clear()
        before = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            pending = update_receiving_address(
                tenant=self.tenant,
                user=self.teacher_user,
                raw_address=self.ADDR_2,
                current_password="CorrectPassword123!",
            )
        after = timezone.now()

        self.assertEqual(pending.status, PaymentAddress.Status.PENDING_ACTIVATION)
        self.assertEqual(pending.address, self.ADDR_2)
        self.assertGreaterEqual(pending.activates_at, before + timedelta(hours=24))
        self.assertLessEqual(pending.activates_at, after + timedelta(hours=24))

        # Existing ACTIVE address stays active during cooldown
        active = PaymentAddress.objects.get(
            tenant=self.tenant,
            provider="crypto_usdt",
            status=PaymentAddress.Status.ACTIVE,
        )
        self.assertEqual(active.address, self.ADDR_1)

        # Security alert email dispatched to teacher's verified email
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.teacher_user.email])
        self.assertIn(self.ADDR_2, sent.body)
        self.assertIn("24", sent.body)

    def test_same_active_address_rejected_without_cooldown_or_email(self):
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_1,
            status=PaymentAddress.Status.ACTIVE,
            activates_at=timezone.now() - timedelta(days=1),
        )

        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(ValidationError):
                update_receiving_address(
                    tenant=self.tenant,
                    user=self.teacher_user,
                    raw_address=self.ADDR_1.lower(),
                    current_password="CorrectPassword123!",
                )

        self.assertEqual(
            PaymentAddress.objects.filter(
                tenant=self.tenant,
                status=PaymentAddress.Status.PENDING_ACTIVATION,
            ).count(),
            0,
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_replacing_pending_address_maintains_single_pending_invariant(self):
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_1,
            status=PaymentAddress.Status.ACTIVE,
            activates_at=timezone.now() - timedelta(days=1),
        )
        with self.captureOnCommitCallbacks(execute=True):
            first_pending = update_receiving_address(
                tenant=self.tenant,
                user=self.teacher_user,
                raw_address=self.ADDR_2,
                current_password="CorrectPassword123!",
            )

        with self.captureOnCommitCallbacks(execute=True):
            second_pending = update_receiving_address(
                tenant=self.tenant,
                user=self.teacher_user,
                raw_address=self.ADDR_3,
                current_password="CorrectPassword123!",
            )

        pending_qs = PaymentAddress.objects.filter(
            tenant=self.tenant,
            provider="crypto_usdt",
            status=PaymentAddress.Status.PENDING_ACTIVATION,
        )
        self.assertEqual(pending_qs.count(), 1)
        self.assertEqual(pending_qs.first().id, second_pending.id)
        self.assertEqual(pending_qs.first().address, self.ADDR_3)

        first_pending.refresh_from_db()
        self.assertEqual(first_pending.status, PaymentAddress.Status.RETIRED)
        self.assertIsNotNone(first_pending.retired_at)

    def test_reactivating_retired_address_resets_to_pending_with_24h_cooldown(self):
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_1,
            status=PaymentAddress.Status.ACTIVE,
            activates_at=timezone.now() - timedelta(days=2),
        )
        retired = PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_2,
            status=PaymentAddress.Status.RETIRED,
            activates_at=timezone.now() - timedelta(days=10),
            retired_at=timezone.now() - timedelta(days=2),
        )

        mail.outbox.clear()
        before = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            reactivated = update_receiving_address(
                tenant=self.tenant,
                user=self.teacher_user,
                raw_address=self.ADDR_2,
                current_password="CorrectPassword123!",
            )
        after = timezone.now()

        self.assertEqual(reactivated.id, retired.id)
        self.assertEqual(reactivated.status, PaymentAddress.Status.PENDING_ACTIVATION)
        self.assertIsNone(reactivated.retired_at)
        self.assertGreaterEqual(reactivated.activates_at, before + timedelta(hours=24))
        self.assertLessEqual(reactivated.activates_at, after + timedelta(hours=24))
        self.assertEqual(len(mail.outbox), 1)

    def test_address_owned_by_another_tenant_rejected(self):
        other_teacher = User.objects.create_user(
            username="other_addr_teacher",
            email="other_addr@example.com",
            password="CorrectPassword123!",
            role=User.Role.TEACHER,
        )
        other_tenant = Tenant.objects.get(teacher_profile=other_teacher.teacher_profile)
        PaymentAddress.objects.create(
            tenant=other_tenant,
            provider="crypto_usdt",
            address=self.ADDR_1,
            status=PaymentAddress.Status.ACTIVE,
        )

        with self.assertRaises(ValidationError):
            update_receiving_address(
                tenant=self.tenant,
                user=self.teacher_user,
                raw_address=self.ADDR_1,
                current_password="CorrectPassword123!",
            )

    def test_security_alert_dispatched_via_async_task_on_commit(self):
        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_1,
            status=PaymentAddress.Status.ACTIVE,
            activates_at=timezone.now() - timedelta(days=1),
        )

        with patch(
            "payments.tasks.safe_send_receiving_address_change_alert"
        ) as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                pending = update_receiving_address(
                    tenant=self.tenant,
                    user=self.teacher_user,
                    raw_address=self.ADDR_2,
                    current_password="CorrectPassword123!",
                )
            mock_enqueue.assert_called_once_with(pending.id)

    def test_security_alert_formats_teacher_timezone_has_priority_html_and_dedups(self):
        from payments.tasks import send_receiving_address_change_alert_task

        self.teacher_user.timezone = "Asia/Tehran"
        self.teacher_user.save(update_fields=["timezone"])

        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_1,
            status=PaymentAddress.Status.ACTIVE,
            activates_at=timezone.now() - timedelta(days=1),
        )

        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            pending = update_receiving_address(
                tenant=self.tenant,
                user=self.teacher_user,
                raw_address=self.ADDR_2,
                current_password="CorrectPassword123!",
            )

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn("Asia/Tehran", sent.body)
        self.assertEqual(sent.extra_headers.get("X-Priority"), "1")
        self.assertEqual(sent.extra_headers.get("Importance"), "high")
        self.assertEqual(len(sent.alternatives), 1)
        self.assertEqual(sent.alternatives[0][1], "text/html")

        # Duplicate invocation for the same scheduled change is deduplicated
        send_receiving_address_change_alert_task(pending.id)
        self.assertEqual(len(mail.outbox), 1)

    def test_security_alert_skipped_when_teacher_email_not_verified(self):
        self.teacher_user.is_email_verified = False
        self.teacher_user.save(update_fields=["is_email_verified"])

        PaymentAddress.objects.create(
            tenant=self.tenant,
            provider="crypto_usdt",
            address=self.ADDR_1,
            status=PaymentAddress.Status.ACTIVE,
            activates_at=timezone.now() - timedelta(days=1),
        )

        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            update_receiving_address(
                tenant=self.tenant,
                user=self.teacher_user,
                raw_address=self.ADDR_2,
                current_password="CorrectPassword123!",
            )

        self.assertEqual(len(mail.outbox), 0)
