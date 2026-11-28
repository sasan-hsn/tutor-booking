import logging

import sentry_sdk
from celery import shared_task

from wallet.services import reconcile_wallet_balances

logger = logging.getLogger(__name__)


def _emit_reconciliation_sentry_alert(discrepancy: dict) -> None:
    """Emit a critical-severity Sentry event for a single wallet reconciliation mismatch."""
    discrepancy_summary = (
        f"settled={discrepancy['settled_discrepancy']}, "
        f"reserved={discrepancy['reserved_discrepancy']}"
    )
    payload = {
        "wallet_id": discrepancy["wallet_id"],
        "user_id": discrepancy["user_id"],
        "tenant_id": discrepancy["tenant_id"],
        "currency": discrepancy["currency"],
        "cached_settled": f"{discrepancy['cached_settled']:.2f}",
        "expected_settled": f"{discrepancy['expected_settled']:.2f}",
        "cached_reserved": f"{discrepancy['cached_reserved']:.2f}",
        "expected_reserved": f"{discrepancy['expected_reserved']:.2f}",
        "settled_discrepancy": f"{discrepancy['settled_discrepancy']:.2f}",
        "reserved_discrepancy": f"{discrepancy['reserved_discrepancy']:.2f}",
    }

    with sentry_sdk.new_scope() as scope:
        scope.set_tag("tenant_id", str(discrepancy["tenant_id"]))
        scope.set_tag("wallet_id", str(discrepancy["wallet_id"]))
        scope.set_tag("discrepancy", discrepancy_summary)
        scope.set_context("wallet_reconciliation", payload)
        sentry_sdk.capture_message(
            (
                f"Wallet balance reconciliation mismatch detected for "
                f"wallet_id={discrepancy['wallet_id']} "
                f"(tenant_id={discrepancy['tenant_id']}, "
                f"user_id={discrepancy['user_id']}): {discrepancy_summary}"
            ),
            level="fatal",
        )


@shared_task(name="wallet.tasks.reconcile_wallet_balances_task")
def reconcile_wallet_balances_task() -> int:
    """
    Daily Celery Beat periodic task that audits and reconciles cached wallet balances
    against the immutable ledger. Emits critical logs and Sentry alerts on any discrepancy.
    Returns the total number of wallets with discrepancies.
    """
    logger.info("Starting daily wallet balance reconciliation task sweep.")
    discrepancies = reconcile_wallet_balances()

    if discrepancies:
        for discrepancy in discrepancies:
            _emit_reconciliation_sentry_alert(discrepancy)
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
