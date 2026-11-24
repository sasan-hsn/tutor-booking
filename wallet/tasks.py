import logging

from celery import shared_task

from wallet.services import reconcile_wallet_balances

logger = logging.getLogger(__name__)


@shared_task(name="wallet.tasks.reconcile_wallet_balances_task")
def reconcile_wallet_balances_task() -> int:
    """
    Daily Celery Beat periodic task that audits and reconciles cached wallet balances
    against the immutable ledger. Flags and logs critical errors on any detected discrepancy.
    Returns the total number of wallets with discrepancies.
    """
    logger.info("Starting daily wallet balance reconciliation task sweep.")
    discrepancies = reconcile_wallet_balances()

    if discrepancies:
        mismatched_ids = [d["wallet_id"] for d in discrepancies]
        logger.critical(
            "Reconciliation task completed with %d wallet balance discrepancies detected! Mismatched wallet IDs: %s",
            len(discrepancies),
            mismatched_ids,
        )
    else:
        logger.info(
            "Reconciliation task completed successfully: all wallet caches match ledger entries."
        )

    return len(discrepancies)
