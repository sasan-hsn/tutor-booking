import logging
import smtplib
import uuid

import requests
from anymail.exceptions import AnymailAPIError
from celery import shared_task
from django.contrib.auth import get_user_model

from .emails import send_chat_unread_digest_email
from .models import Conversation, Message
from .presence import is_user_in_room
from .services import clear_digest_debounce_flag

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
        return getattr(exc, "status_code", None) in (429, 500, 502, 503, 504)
    if isinstance(exc, TRANSIENT_EMAIL_ERRORS):
        return True
    return False


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_unread_message_digest(self, conversation_id, recipient_id):
    """
    Evaluates unread chat messages for recipient in conversation after a 15-minute cooldown.
    If unread messages exist and the recipient is not currently connected to the room via WebSocket,
    dispatches a consolidated email digest.
    """
    if not conversation_id or not recipient_id:
        logger.warning(
            "send_unread_message_digest called with invalid params: conv=%s, recipient=%s",
            conversation_id,
            recipient_id,
        )
        return

    try:
        conv_uuid = uuid.UUID(str(conversation_id))
    except (ValueError, TypeError):
        logger.warning(
            "Invalid conversation_id '%s' passed to send_unread_message_digest.",
            conversation_id,
        )
        return

    try:
        conversation = Conversation.objects.select_related("student", "teacher").get(
            id=conv_uuid
        )
    except Conversation.DoesNotExist:
        logger.warning(
            "Conversation %s not found; skipping unread message digest.", conv_uuid
        )
        return

    if recipient_id not in (conversation.student_id, conversation.teacher_id):
        logger.warning(
            "Recipient #%s is not a participant in conversation %s; skipping unread message digest.",
            recipient_id,
            conv_uuid,
        )
        return

    recipient = (
        conversation.student
        if recipient_id == conversation.student_id
        else conversation.teacher
    )
    sender = conversation.get_counterpart(recipient)

    # 1. Re-check unread messages from counterpart
    watermark = conversation.get_last_read_message_id(recipient)
    unread_messages = list(
        conversation.messages.filter(
            id__gt=watermark,
            message_type=Message.MessageType.TEXT,
        )
        .exclude(sender=recipient)
        .select_related("sender")
        .order_by("id")
    )

    if not unread_messages:
        logger.info(
            "No unread messages for recipient #%s in conversation %s; exiting silently.",
            recipient.id,
            conversation.id,
        )
        clear_digest_debounce_flag(recipient.id, conversation.id)
        return

    # 2. Re-check active room presence (WebSocket connection in the conversation room)
    if is_user_in_room(conversation.id, recipient.id):
        logger.info(
            "Recipient #%s has active room presence in conversation %s; exiting silently.",
            recipient.id,
            conversation.id,
        )
        return

    # 3. Deliver consolidated email digest
    try:
        send_chat_unread_digest_email(
            recipient=recipient,
            sender=sender,
            conversation=conversation,
            unread_messages=unread_messages,
        )
        # Clear the debounce flag so future messages can schedule another cycle
        clear_digest_debounce_flag(recipient.id, conversation.id)
    except Exception as exc:
        if is_transient_email_error(exc):
            logger.exception(
                "Transient error sending unread message digest to user #%s for conv %s. Retrying...",
                recipient.id,
                conversation.id,
            )
            raise self.retry(exc=exc)
        logger.exception(
            "Non-transient error sending unread message digest to user #%s for conv %s.",
            recipient.id,
            conversation.id,
        )
        raise
