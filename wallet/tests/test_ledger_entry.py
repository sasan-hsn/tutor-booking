from decimal import Decimal

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from accounts.models import Tenant, User
from wallet.models import LedgerEntry, Wallet


class LedgerEntryModelTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_mary",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)
        self.student = User.objects.create_user(
            username="student_alice",
            password="testpassword",
            role=User.Role.STUDENT,
        )
        self.wallet = Wallet.objects.create(
            tenant=self.tenant,
            user=self.student,
            settled_balance=Decimal("100.00"),
            reserved_balance=Decimal("0.00"),
        )

    def test_create_valid_ledger_entry(self):
        entry = LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=self.wallet,
            entry_type=LedgerEntry.EntryType.CREDIT,
            amount=Decimal("100.00"),
            settled_after=Decimal("100.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="tx_test_create_001",
            reference_id="ref_001",
            description="Initial top up",
        )
        self.assertIsNotNone(entry.pk)
        self.assertEqual(entry.amount, Decimal("100.00"))
        self.assertEqual(entry.entry_type, "CREDIT")
        self.assertEqual(str(entry), f"LedgerEntry({entry.id}, CREDIT, 100.00)")

    def test_amount_positive_check_constraint(self):
        """Verifies DB CheckConstraint raises IntegrityError when amount <= 0."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerEntry.objects.create(
                    tenant=self.tenant,
                    wallet=self.wallet,
                    entry_type=LedgerEntry.EntryType.CREDIT,
                    amount=Decimal("0.00"),
                    settled_after=Decimal("100.00"),
                    reserved_after=Decimal("0.00"),
                    idempotency_key="tx_test_zero_amount",
                    reference_id="ref_zero",
                    description="Zero amount test",
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerEntry.objects.create(
                    tenant=self.tenant,
                    wallet=self.wallet,
                    entry_type=LedgerEntry.EntryType.CREDIT,
                    amount=Decimal("-10.00"),
                    settled_after=Decimal("100.00"),
                    reserved_after=Decimal("0.00"),
                    idempotency_key="tx_test_negative_amount",
                    reference_id="ref_neg",
                    description="Negative amount test",
                )

    def test_unique_idempotency_key(self):
        """Verifies unique constraint on idempotency_key."""
        LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=self.wallet,
            entry_type=LedgerEntry.EntryType.CREDIT,
            amount=Decimal("20.00"),
            settled_after=Decimal("120.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="dup_key_001",
            reference_id="ref_dup_1",
            description="First entry",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerEntry.objects.create(
                    tenant=self.tenant,
                    wallet=self.wallet,
                    entry_type=LedgerEntry.EntryType.CREDIT,
                    amount=Decimal("20.00"),
                    settled_after=Decimal("140.00"),
                    reserved_after=Decimal("0.00"),
                    idempotency_key="dup_key_001",
                    reference_id="ref_dup_2",
                    description="Duplicate entry",
                )

    def test_on_delete_protect_tenant(self):
        """Deleting Tenant with ledger entries must be blocked by models.PROTECT."""
        LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=self.wallet,
            entry_type=LedgerEntry.EntryType.CREDIT,
            amount=Decimal("50.00"),
            settled_after=Decimal("150.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="protect_tenant_key",
            reference_id="ref_prot_tenant",
            description="Protect tenant test",
        )
        with self.assertRaises(ProtectedError):
            self.tenant.delete()

    def test_on_delete_protect_wallet(self):
        """Deleting Wallet with ledger entries must be blocked by models.PROTECT."""
        LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=self.wallet,
            entry_type=LedgerEntry.EntryType.CREDIT,
            amount=Decimal("50.00"),
            settled_after=Decimal("150.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="protect_wallet_key",
            reference_id="ref_prot_wallet",
            description="Protect wallet test",
        )
        with self.assertRaises(ProtectedError):
            self.wallet.delete()


class LedgerEntryImmutabilityTriggerTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_trigger",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)
        self.student = User.objects.create_user(
            username="student_trigger",
            password="testpassword",
            role=User.Role.STUDENT,
        )
        self.wallet = Wallet.objects.create(
            tenant=self.tenant,
            user=self.student,
            settled_balance=Decimal("50.00"),
            reserved_balance=Decimal("0.00"),
        )
        self.entry = LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=self.wallet,
            entry_type=LedgerEntry.EntryType.CREDIT,
            amount=Decimal("50.00"),
            settled_after=Decimal("50.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="trigger_test_key_001",
            reference_id="ref_trigger_001",
            description="Trigger immutability test",
        )

    def test_raw_sql_update_aborts_with_internal_error(self):
        """Raw SQL UPDATE wallet_ledgerentry SET amount = 100 WHERE id = ... aborts via trigger."""
        with self.assertRaises(DatabaseError) as ctx:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE wallet_ledgerentry SET amount = 100 WHERE id = %s;",
                        [self.entry.id],
                    )
        self.assertIn("LedgerEntry is immutable", str(ctx.exception))

    def test_raw_sql_delete_aborts_with_internal_error(self):
        """Raw SQL DELETE FROM wallet_ledgerentry WHERE id = ... aborts via trigger."""
        with self.assertRaises(DatabaseError) as ctx:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM wallet_ledgerentry WHERE id = %s;",
                        [self.entry.id],
                    )
        self.assertIn("LedgerEntry is immutable", str(ctx.exception))

    def test_orm_save_update_aborts(self):
        """Attempting to update an existing LedgerEntry via ORM save() aborts."""
        self.entry.amount = Decimal("999.00")
        with self.assertRaises(DatabaseError) as ctx:
            with transaction.atomic():
                self.entry.save()
        self.assertIn("LedgerEntry is immutable", str(ctx.exception))

    def test_orm_delete_aborts(self):
        """Attempting to delete a LedgerEntry via ORM delete() aborts."""
        with self.assertRaises(DatabaseError) as ctx:
            with transaction.atomic():
                self.entry.delete()
        self.assertIn("LedgerEntry is immutable", str(ctx.exception))
