from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from wallet.exceptions import WalletError
from wallet.management.commands._utils import get_active_superuser
from wallet.models import Wallet
from wallet.services import adjust_wallet


class Command(BaseCommand):
    help = (
        "Executes an administrative balance adjustment (credit or debit) for a wallet."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--wallet-id",
            type=int,
            required=True,
            help="Primary key of the wallet to adjust.",
        )
        parser.add_argument(
            "--amount",
            type=str,
            required=True,
            help="Adjustment amount as a positive Decimal string (e.g. '25.00').",
        )
        parser.add_argument(
            "--direction",
            type=str,
            required=True,
            choices=["credit", "debit"],
            help="Direction of adjustment ('credit' or 'debit').",
        )
        parser.add_argument(
            "--reason",
            type=str,
            required=True,
            help="Mandatory justification/audit reason for the adjustment.",
        )
        parser.add_argument(
            "--admin",
            type=str,
            required=True,
            help="Username of the active superuser authorizing this adjustment.",
        )

    def handle(self, *args, **options):
        admin_user = get_active_superuser(options["admin"])
        wallet_id = options["wallet_id"]
        raw_amount = options["amount"]

        try:
            amount = Decimal(raw_amount)
        except (InvalidOperation, TypeError):
            raise CommandError(
                f"Invalid amount {raw_amount!r}; must be a valid Decimal."
            )

        direction = options["direction"]
        reason = options["reason"]

        try:
            with transaction.atomic():
                try:
                    wallet = (
                        Wallet.objects.select_for_update()
                        .select_related("tenant")
                        .get(pk=wallet_id)
                    )
                except Wallet.DoesNotExist:
                    raise CommandError(f"Wallet with ID {wallet_id} does not exist.")

                entry = adjust_wallet(
                    tenant=wallet.tenant,
                    wallet=wallet,
                    amount=amount,
                    direction=direction,
                    reason=reason,
                    admin_user=admin_user,
                )
        except (ValueError, WalletError) as exc:
            raise CommandError(str(exc))
        except (ValueError, WalletError) as exc:
            raise CommandError(str(exc))

        wallet.refresh_from_db()
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully adjusted wallet {wallet.id} ({direction} {amount} {wallet.currency}). "
                f"LedgerEntry ID: {entry.id}. New settled: {wallet.settled_balance}, "
                f"available: {wallet.available_balance}."
            )
        )
