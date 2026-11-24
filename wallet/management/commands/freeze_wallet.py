from django.core.management.base import BaseCommand, CommandError

from wallet.models import Wallet
from wallet.services import freeze_wallet


class Command(BaseCommand):
    help = "Freezes or unfreezes a wallet to control balance reservation actions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--wallet-id",
            type=int,
            required=True,
            help="Primary key of the wallet to freeze or unfreeze.",
        )
        parser.add_argument(
            "--unfreeze",
            action="store_true",
            default=False,
            help="Unfreeze the wallet instead of freezing it.",
        )

    def handle(self, *args, **options):
        wallet_id = options["wallet_id"]
        unfreeze = options["unfreeze"]
        new_is_frozen = not unfreeze

        try:
            freeze_wallet(wallet_id=wallet_id, is_frozen=new_is_frozen)
        except Wallet.DoesNotExist:
            raise CommandError(f"Wallet with ID {wallet_id} does not exist.")

        action_str = "unfrozen" if unfreeze else "frozen"
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully {action_str} wallet {wallet_id} (is_frozen={new_is_frozen})."
            )
        )
