import logging
from typing import Any, Optional
from django.core.exceptions import DisallowedHost
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

logger = logging.getLogger(__name__)


def strip_sensitive_user_data(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """
    Ensure user context in Sentry events contains only the surrogate ID.
    Strips raw email addresses, usernames, and IP addresses to enforce privacy.
    """
    user = event.get("user")
    if isinstance(user, dict):
        user.pop("email", None)
        user.pop("username", None)
        user.pop("ip_address", None)
    return event


def get_sentry_integrations() -> list[Any]:
    """Return standard Sentry integrations for Django, Celery, and logging."""
    return [
        DjangoIntegration(),
        CeleryIntegration(monitor_beat_tasks=True),
        LoggingIntegration(
            level=logging.INFO,
            event_level=logging.ERROR,
        ),
    ]


def init_sentry(
    dsn: Optional[str] = None,
    environment: Optional[str] = None,
    release: Optional[str] = None,
    traces_sample_rate: Optional[float] = None,
    debug: bool = False,
) -> bool:
    """
    Initialize Sentry SDK with production error monitoring settings.
    Returns True if initialized, False if DSN was empty or not provided.
    """
    if not dsn:
        return False

    env = environment or ("production" if not debug else "development")
    try:
        sample_rate = 0.0 if traces_sample_rate is None else float(traces_sample_rate)
    except (ValueError, TypeError):
        sample_rate = 0.0

    sentry_sdk.init(
        dsn=dsn,
        integrations=get_sentry_integrations(),
        environment=env,
        release=release,
        traces_sample_rate=sample_rate,
        send_default_pii=False,
        ignore_errors=[DisallowedHost],
        before_send=strip_sensitive_user_data,
    )
    return True


class SentryUserContextMiddleware:
    """
    Attach the surrogate user ID (pk) to the Sentry scope for authenticated requests.
    Clears user context for anonymous requests. Safe no-op when Sentry is not initialized.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if sentry_sdk.is_initialized():
            user = getattr(request, "user", None)
            if user and getattr(user, "is_authenticated", False) and getattr(user, "pk", None):
                sentry_sdk.set_user({"id": str(user.pk)})
            else:
                sentry_sdk.set_user(None)
        return self.get_response(request)
