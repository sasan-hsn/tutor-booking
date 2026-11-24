import logging
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import Tenant, User
from wallet.services import credit_wallet, get_or_create_wallet


class AdjustWalletCommandTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_cmd",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)
        self.student = User.objects.create_user(
            username="student_cmd",
            password="testpassword",
            role=User.Role.STUDENT,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student)

        self.superuser = User.objects.create_superuser(
            username="admin_cmd",
            password="testpassword",
            email="admin_cmd@test.local",
        )
        self.regular_user = User.objects.create_user(
            username="regular_cmd",
            password="testpassword",
            role=User.Role.STUDENT,
        )

    def test_non_superuser_rejected(self):
        out = StringIO()
        err = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "adjust_wallet",
                wallet_id=self.wallet.id,
                amount="10.00",
                direction="credit",
                reason="Test reason",
                admin=self.regular_user.username,
                stdout=out,
                stderr=err,
            )
        self.assertIn("superuser", str(ctx.exception).lower())

    def test_non_existent_admin_rejected(self):
        out = StringIO()
        err = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "adjust_wallet",
                wallet_id=self.wallet.id,
                amount="10.00",
                direction="credit",
                reason="Test reason",
                admin="ghost_user",
                stdout=out,
                stderr=err,
            )
        self.assertIn("does not exist", str(ctx.exception).lower())

    def test_credit_adjustment_success(self):
        out = StringIO()
        call_command(
            "adjust_wallet",
            wallet_id=self.wallet.id,
            amount="45.50",
            direction="credit",
            reason="Goodwill credit adjustment",
            admin=self.superuser.username,
            stdout=out,
        )
        self.assertIn("successfully adjusted", out.getvalue().lower())

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("45.50"))
        self.assertEqual(self.wallet.available_balance, Decimal("45.50"))

    def test_debit_adjustment_success(self):
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("100.00"),
            idempotency_key="cmd-debit-init",
            reference_id="ref-cmd-debit",
            description="Init",
        )

        out = StringIO()
        call_command(
            "adjust_wallet",
            wallet_id=self.wallet.id,
            amount="25.00",
            direction="debit",
            reason="Debit clawback",
            admin=self.superuser.username,
            stdout=out,
        )
        self.assertIn("successfully adjusted", out.getvalue().lower())

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("75.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("75.00"))

    def test_debit_insufficient_balance_rejected(self):
        out = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "adjust_wallet",
                wallet_id=self.wallet.id,
                amount="25.00",
                direction="debit",
                reason="Debit exceeding balance",
                admin=self.superuser.username,
                stdout=out,
            )
        self.assertIn("insufficient", str(ctx.exception).lower())


class FreezeWalletCommandTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_freeze",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)
        self.student = User.objects.create_user(
            username="student_freeze",
            password="testpassword",
            role=User.Role.STUDENT,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student)

    def test_freeze_wallet_freezes_successfully(self):
        out = StringIO()
        self.assertFalse(self.wallet.is_frozen)

        with self.assertLogs("wallet.services", level=logging.INFO) as log_context:
            call_command("freeze_wallet", wallet_id=self.wallet.id, stdout=out)

        self.wallet.refresh_from_db()
        self.assertTrue(self.wallet.is_frozen)
        self.assertIn("frozen=True", out.getvalue())
        self.assertIn(str(self.wallet.id), "\n".join(log_context.output))

    def test_freeze_wallet_unfreezes_successfully(self):
        self.wallet.is_frozen = True
        self.wallet.save(update_fields=["is_frozen"])

        out = StringIO()
        with self.assertLogs("wallet.services", level=logging.INFO) as log_context:
            call_command(
                "freeze_wallet", wallet_id=self.wallet.id, unfreeze=True, stdout=out
            )

        self.wallet.refresh_from_db()
        self.assertFalse(self.wallet.is_frozen)
        self.assertIn("frozen=False", out.getvalue())
        self.assertIn(str(self.wallet.id), "\n".join(log_context.output))

    def test_freeze_wallet_non_existent_wallet_fails(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("freeze_wallet", wallet_id=999999)
        self.assertIn("does not exist", str(ctx.exception).lower())


class AuditWalletCommandTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_audit_cmd",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)
        self.student = User.objects.create_user(
            username="student_audit_cmd",
            password="testpassword",
            role=User.Role.STUDENT,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student)

    def test_audit_wallet_clean(self):
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="cmd-audit-clean-1",
            reference_id="ref-clean",
            description="Credit",
        )
        out = StringIO()
        call_command("audit_wallet", wallet_id=self.wallet.id, stdout=out)
        output = out.getvalue()
        self.assertIn("no discrepancy", output.lower())
        self.assertIn("50.00", output)

    def test_audit_wallet_detects_artificially_introduced_mismatch(self):
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("50.00"),
            idempotency_key="cmd-audit-disc-1",
            reference_id="ref-disc",
            description="Credit",
        )
        # Artificially modify cached balance
        self.wallet.settled_balance = Decimal("75.00")
        self.wallet.save(update_fields=["settled_balance"])

        out = StringIO()
        call_command("audit_wallet", wallet_id=self.wallet.id, stdout=out)
        output = out.getvalue()
        self.assertIn("discrepancy detected", output.lower())
        self.assertIn("75.00", output)
        self.assertIn("50.00", output)

    def test_audit_wallet_non_existent_wallet_fails(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("audit_wallet", wallet_id=999999)
        self.assertIn("does not exist", str(ctx.exception).lower())


class RepairWalletCacheCommandTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_repair_cmd",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)
        self.student = User.objects.create_user(
            username="student_repair_cmd",
            password="testpassword",
            role=User.Role.STUDENT,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student)
        self.superuser = User.objects.create_superuser(
            username="super_repair",
            password="testpassword",
            email="super_repair@test.local",
        )

    def test_repair_wallet_cache_dry_run_does_not_commit(self):
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("60.00"),
            idempotency_key="cmd-repair-dry-1",
            reference_id="ref-dry",
            description="Credit",
        )
        self.wallet.settled_balance = Decimal("99.00")
        self.wallet.save(update_fields=["settled_balance"])

        out = StringIO()
        call_command("repair_wallet_cache", wallet_id=self.wallet.id, stdout=out)
        output = out.getvalue()
        self.assertIn("dry run", output.lower())

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("99.00"))

    def test_repair_wallet_cache_commit_restores_snapshot(self):
        credit_wallet(
            tenant=self.tenant,
            wallet=self.wallet,
            amount=Decimal("60.00"),
            idempotency_key="cmd-repair-commit-1",
            reference_id="ref-commit",
            description="Credit",
        )
        self.wallet.settled_balance = Decimal("99.00")
        self.wallet.save(update_fields=["settled_balance"])

        out = StringIO()
        call_command(
            "repair_wallet_cache",
            wallet_id=self.wallet.id,
            commit=True,
            admin=self.superuser.username,
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("repaired", output.lower())

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.settled_balance, Decimal("60.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
