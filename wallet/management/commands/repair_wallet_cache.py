from django.core.management.base import BaseCommand, CommandError

from wallet.management.commands._utils import get_active_superuser
from wallet.models import Wallet
from wallet.services import repair_wallet_cache


class Command(BaseCommand):
    help = "Recalculates and repairs cached wallet balance snapshots from immutable ledger entries."

    def add_arguments(self, parser):
        parser.add_argument(
            "--wallet-id",
            type=int,
            required=True,
            help="Primary key of the wallet to repair.",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help="Apply the repairs to the database. Without this, operates in dry-run mode.",
        )
        parser.add_argument(
            "--admin",
            type=str,
            default=None,
            help="Optional username of the superuser authorizing the repair (for audit logging).",
        )

    def handle(self, *args, **options):
        wallet_id = options["wallet_id"]
        commit = options["commit"]
        admin_username = options.get("admin")

        admin_user = get_active_superuser(admin_username) if admin_username else None

        try:
            result = repair_wallet_cache(
                wallet_id=wallet_id,
                admin_user=admin_user,
                commit=commit,
            )
        except Wallet.DoesNotExist:
            raise CommandError(f"Wallet with ID {wallet_id} does not exist.")

        has_discrepancy = result["has_discrepancy"]
        if not has_discrepancy:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Wallet {wallet_id} cache is already consistent with ledger entries. "
                    "No repairs needed."
                )
            )
            return

        if not commit:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Discrepancy detected for Wallet {wallet_id}:\n"
                    f"  Settled:  cached={result['settled_before']} -> expected={result['expected_settled']}\n"
                    f"  Reserved: cached={result['reserved_before']} -> expected={result['expected_reserved']}\n"
                    "Changes were NOT committed. Pass '--commit' to persist repairs."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully repaired cache for Wallet {wallet_id}:\n"
                    f"  Settled:  {result['settled_before']} -> {result['settled_after']}\n"
                    f"  Reserved: {result['reserved_before']} -> {result['reserved_after']}"
                )
            )
