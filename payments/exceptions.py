from django.core.exceptions import ValidationError

from wallet.exceptions import InsufficientBalanceError, WalletError


class PayoutError(WalletError):
    """Base exception for all payout domain errors."""

    status_code: int = 400


class PayoutConflictError(PayoutError):
    """
    Raised when an operation conflicts with the current payout state (HTTP 409).
    Used across payout lifecycle state transitions when a requested transition is invalid.
    """

    status_code: int = 409


class PayoutInsufficientBalanceError(
    PayoutError, InsufficientBalanceError, ValidationError
):
    """
    Raised when student available balance is insufficient for a payout operation.
    Inherits from PayoutError, InsufficientBalanceError, and ValidationError.
    """

    status_code: int = 400


class TopupOrderConflictError(ValidationError):
    """Raised when a top-up order cannot be claimed in its current state (HTTP 409)."""

    status_code: int = 409
