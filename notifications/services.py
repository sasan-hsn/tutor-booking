import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

from .models import Notification

logger = logging.getLogger(__name__)


def create_and_send_notification(
    recipient,
    event_type,
    title,
    message,
    actor=None,
    level=Notification.Level.INFO,
    action_url="",
    metadata=None,
):
    """
    Synchronously create a Notification record in the active transaction,
    then broadcast the notification payload to the recipient's private
    channel group strictly upon transaction commit.
    """
    notification = Notification.objects.create(
        recipient=recipient,
        actor=actor,
        event_type=event_type,
        level=level,
        title=title,
        message=message,
        action_url=action_url,
        metadata=metadata if metadata is not None else {},
    )

    def broadcast():
        channel_layer = get_channel_layer()
        if not channel_layer:
            return

        try:
            unread_count = Notification.objects.unread_count_for(recipient)
            group_name = Notification.get_group_name(recipient.id)
            payload = notification.to_ws_payload(unread_count)
            async_to_sync(channel_layer.group_send)(group_name, payload)
        except Exception:
            logger.exception(
                "Failed to broadcast notification #%s to channel layer",
                notification.id,
            )

    transaction.on_commit(broadcast)
    return notification
