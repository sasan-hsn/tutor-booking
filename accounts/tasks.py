import logging
import smtplib
from anymail.exceptions import AnymailAPIError
from celery import shared_task
from django.contrib.auth import get_user_model
import requests

from . import emails

logger = logging.getLogger(__name__)
User = get_user_model()

TRANSIENT_EMAIL_ERRORS = (
    smtplib.SMTPException,
    ConnectionError,
    TimeoutError,
    OSError,
    requests.exceptions.RequestException,
    AnymailAPIError,
)


def is_transient_email_error(exc: Exception) -> bool:
    """Determine whether an email sending exception is temporary and safe to retry."""
    if isinstance(exc, AnymailAPIError):
        return getattr(exc, 'status_code', None) in (429, 500, 502, 503, 504)
    if isinstance(exc, TRANSIENT_EMAIL_ERRORS):
        return True
    return False


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_verification_email_task(self, user_id: int, next_url: str | None = None):
    """Deliver verification email to newly registered or unverified user."""
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("User #%s not found; skipping verification email task.", user_id)
        return

    if user.is_email_verified:
        logger.info("User #%s is already verified; skipping verification email task.", user_id)
        return

    try:
        emails.send_verification_email(user, next_url=next_url)
    except Exception as exc:
        if is_transient_email_error(exc):
            logger.exception("Transient error sending verification email to user #%s. Retrying...", user_id)
            raise self.retry(exc=exc)
        logger.exception("Non-transient error sending verification email to user #%s.", user_id)
        raise


def safe_send_verification_email(user_id: int, next_url: str | None = None):
    """Safely enqueue verification email task without crashing caller on broker error."""
    try:
        send_verification_email_task.delay(user_id, next_url=next_url)
    except Exception:
        logger.exception("Failed to enqueue email verification task for user #%s", user_id)
