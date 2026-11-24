from django.core.management.base import BaseCommand, CommandError

from wallet.models import Wallet
from wallet.services import audit_wallet


class Command(BaseCommand):
    help = "Audits a wallet by computing expected balances from its immutable ledger entries."

    def add_arguments(self, parser):
        parser.add_argument(
            "--wallet-id",
            type=int,
            required=True,
            help="Primary key of the wallet to audit.",
        )

    def handle(self, *args, **options):
        wallet_id = options["wallet_id"]
        try:
            wallet = Wallet.objects.select_related("user", "tenant").get(pk=wallet_id)
        except Wallet.DoesNotExist:
            raise CommandError(f"Wallet with ID {wallet_id} does not exist.")

        audit_res = audit_wallet(wallet=wallet)

        self.stdout.write("=" * 60)
        self.stdout.write(f"Wallet Audit Report: ID {wallet.id}")
        self.stdout.write("=" * 60)
        self.stdout.write(
            f"User:           {wallet.user.username} (ID: {wallet.user_id})"
        )
        self.stdout.write(
            f"Tenant:         {wallet.tenant.name} (ID: {wallet.tenant_id})"
        )
        self.stdout.write(f"Currency:       {wallet.currency}")
        self.stdout.write(f"Frozen:         {wallet.is_frozen}")
        self.stdout.write("-" * 60)
        self.stdout.write(f"Cached Settled:      {audit_res['cached_settled']:>12}")
        self.stdout.write(f"Expected Settled:    {audit_res['expected_settled']:>12}")
        self.stdout.write(
            f"Settled Discrepancy: {audit_res['settled_discrepancy']:>12}"
        )
        self.stdout.write("-" * 60)
        self.stdout.write(f"Cached Reserved:     {audit_res['cached_reserved']:>12}")
        self.stdout.write(f"Expected Reserved:   {audit_res['expected_reserved']:>12}")
        self.stdout.write(
            f"Reserved Discrepancy:{audit_res['reserved_discrepancy']:>12}"
        )
        self.stdout.write("=" * 60)

        if audit_res["has_discrepancy"]:
            self.stdout.write(
                self.style.ERROR(
                    f"DISCREPANCY DETECTED on Wallet {wallet.id}! "
                    f"Settled delta: {audit_res['settled_discrepancy']}, "
                    f"Reserved delta: {audit_res['reserved_discrepancy']}. "
                    "Run 'repair_wallet_cache --commit' to remediate."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"NO DISCREPANCY DETECTED on Wallet {wallet.id}: "
                    "Cached snapshot matches immutable ledger entries exactly."
                )
            )
