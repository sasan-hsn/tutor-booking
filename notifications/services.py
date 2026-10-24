import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

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


def mark_booking_notifications_as_read(recipient, booking_id, event_types=None):
    """
    Mark unread notifications for `recipient` associated with `booking_id` as read.
    Optionally filter by specific `event_types`.
    Schedules a 'notification_read' broadcast to user's Channel group on commit.
    Returns: tuple of (marked_ids: list[int], unread_count: int)
    """
    qs = Notification.objects.filter(
        recipient=recipient,
        is_read=False,
        metadata__booking_id=booking_id,
    )
    if event_types:
        qs = qs.filter(event_type__in=event_types)

    marked_ids = list(qs.values_list('id', flat=True))
    if not marked_ids:
        return [], Notification.objects.unread_count_for(recipient)

    Notification.objects.filter(id__in=marked_ids).update(
        is_read=True,
        read_at=timezone.now(),
    )
    unread_count = Notification.objects.unread_count_for(recipient)

    def broadcast():
        channel_layer = get_channel_layer()
        if not channel_layer:
            return

        try:
            group_name = Notification.get_group_name(recipient.id)
            payload = {
                "type": "notification_read",
                "notification_ids": marked_ids,
                "unread_count": unread_count,
            }
            async_to_sync(channel_layer.group_send)(group_name, payload)
        except Exception:
            logger.exception(
                "Failed to broadcast notification_read for recipient #%s to channel layer",
                recipient.id,
            )

    transaction.on_commit(broadcast)
    return marked_ids, unread_count
