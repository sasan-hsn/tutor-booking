class WalletError(Exception):
    """Base exception for all wallet domain errors."""

    pass


class WalletFrozenError(WalletError):
    """Raised when an operation requiring active status is attempted on a frozen wallet."""

    pass


class InsufficientBalanceError(WalletError):
    """Raised when an operation requests more funds than available in the wallet."""

    pass


class InvalidAmountError(WalletError, ValueError):
    """Raised when an invalid amount (e.g. <= 0 or non-Decimal) is passed to wallet services."""

    pass


class InvalidEntryTypeError(WalletError, ValueError):
    """Raised when an unsupported entry type is passed to a wallet service."""

    pass


class TenantMismatchError(WalletError):
    """Raised when an operation attempts to mutate a wallet belonging to a different tenant."""

    pass


class CurrencyMismatchError(WalletError):
    """Raised when a wallet's currency does not match the tenant's default currency."""

    pass
