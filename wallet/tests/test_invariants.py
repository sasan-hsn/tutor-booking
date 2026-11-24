import random
from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase

from accounts.models import Tenant, User
from wallet.exceptions import InsufficientBalanceError
from wallet.models import LedgerEntry
from wallet.services import credit_wallet, get_or_create_wallet, reserve_funds


class WalletInvariantFuzzingTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher_fuzz",
            password="testpassword",
            role=User.Role.TEACHER,
        )
        self.tenant = Tenant.objects.get(teacher_profile=self.teacher.teacher_profile)
        self.student = User.objects.create_user(
            username="student_fuzz",
            password="testpassword",
            role=User.Role.STUDENT,
        )
        self.wallet = get_or_create_wallet(self.tenant, self.student)

    def test_invariant_fuzzing_100_operations(self):
        """
        Executes 100 randomized sequential operations (credit with random entry_types, reserve)
        and asserts after EVERY operation:
          - wallet.settled_balance == SUM(settled-affecting entries)
          - wallet.reserved_balance == SUM(reserve-affecting entries)
          - wallet.available_balance >= Decimal("0.00")
          - wallet.settled_balance >= wallet.reserved_balance
        """
        rng = random.Random(42)  # Deterministic seed for reproducible test runs
        allowed_credit_types = [
            LedgerEntry.EntryType.CREDIT,
            LedgerEntry.EntryType.WAIVER,
            LedgerEntry.EntryType.ADJUSTMENT_CREDIT,
        ]

        for i in range(100):
            op = rng.choice(["credit", "reserve"])
            # Generate random cents between 1.00 and 50.00
            cents = rng.randint(100, 5000)
            amount = Decimal(cents) / Decimal(100)
            idempotency_key = f"fuzz_op_{i}_{cents}"
            ref_id = f"fuzz_ref_{i}"

            if op == "credit":
                entry_type = rng.choice(allowed_credit_types)
                credit_wallet(
                    tenant=self.tenant,
                    wallet=self.wallet,
                    amount=amount,
                    idempotency_key=idempotency_key,
                    reference_id=ref_id,
                    description=f"Fuzz credit op {i}",
                    entry_type=entry_type,
                )
            else:
                # reserve
                try:
                    reserve_funds(
                        tenant=self.tenant,
                        wallet=self.wallet,
                        amount=amount,
                        idempotency_key=idempotency_key,
                        reference_id=ref_id,
                        description=f"Fuzz reserve op {i}",
                    )
                except InsufficientBalanceError:
                    # Expected when amount > available_balance
                    pass

            # Invariant checks after EVERY operation
            self.wallet.refresh_from_db()

            # Sum settled-affecting entries: CREDIT, WAIVER, ADJUSTMENT_CREDIT
            settled_sum = LedgerEntry.objects.filter(
                wallet=self.wallet,
                entry_type__in=[
                    LedgerEntry.EntryType.CREDIT,
                    LedgerEntry.EntryType.WAIVER,
                    LedgerEntry.EntryType.ADJUSTMENT_CREDIT,
                ],
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

            # Sum reserve-affecting entries: RESERVE
            reserved_sum = LedgerEntry.objects.filter(
                wallet=self.wallet,
                entry_type=LedgerEntry.EntryType.RESERVE,
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

            self.assertEqual(
                self.wallet.settled_balance,
                settled_sum,
                f"Op {i}: settled_balance mismatch: {self.wallet.settled_balance} != {settled_sum}",
            )
            self.assertEqual(
                self.wallet.reserved_balance,
                reserved_sum,
                f"Op {i}: reserved_balance mismatch: {self.wallet.reserved_balance} != {reserved_sum}",
            )
            self.assertGreaterEqual(
                self.wallet.available_balance,
                Decimal("0.00"),
                f"Op {i}: available_balance negative: {self.wallet.available_balance}",
            )
            self.assertGreaterEqual(
                self.wallet.settled_balance,
                self.wallet.reserved_balance,
                f"Op {i}: settled < reserved: {self.wallet.settled_balance} < {self.wallet.reserved_balance}",
            )
