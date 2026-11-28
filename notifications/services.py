import logging
from decimal import Decimal
from typing import NamedTuple

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.urls import reverse
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

    marked_ids = list(qs.values_list("id", flat=True))
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


class WalletToastSpec(NamedTuple):
    level: str
    title: str
    message_suffix: str


_WALLET_CREDIT_TOAST_SPECS: dict[str, WalletToastSpec] = {
    "CREDIT": WalletToastSpec(
        "success",
        "Deposit Credited",
        "has been credited to your wallet.",
    ),
    "WAIVER": WalletToastSpec(
        "success",
        "Penalty Waiver Credited",
        "goodwill waiver has been credited to your wallet.",
    ),
    "ADJUSTMENT_CREDIT": WalletToastSpec(
        "info",
        "Wallet Balance Adjusted",
        "credit adjustment has been applied to your wallet.",
    ),
}


def broadcast_wallet_balance_updated(payload: dict, user_id: int) -> None:
    """Broadcast a live wallet balance update frame to the user's private Channels group."""
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    try:
        group_name = Notification.get_group_name(user_id)
        async_to_sync(channel_layer.group_send)(group_name, payload)
    except Exception:
        logger.exception(
            "Failed to broadcast wallet balance update for user #%s",
            user_id,
        )


def schedule_wallet_balance_updated(
    wallet,
    *,
    entry_id: int,
    entry_type: str,
    amount: Decimal,
) -> None:
    """
    Schedule a live wallet balance Channels push on transaction commit without
    creating a persistent Notification database record.
    """
    spec = _WALLET_CREDIT_TOAST_SPECS.get(
        entry_type,
        WalletToastSpec(
            "info", "Wallet Balance Updated", "has been added to your wallet."
        ),
    )
    payload = {
        "type": "wallet_balance_updated",
        "wallet_id": wallet.id,
        "tenant_id": wallet.tenant_id,
        "entry_id": entry_id,
        "entry_type": entry_type,
        "amount": f"{amount:.2f}",
        "currency": wallet.currency,
        "available_balance": f"{wallet.available_balance:.2f}",
        "reserved_balance": f"{wallet.reserved_balance:.2f}",
        "settled_balance": f"{wallet.settled_balance:.2f}",
        "level": spec.level,
        "title": spec.title,
        "message": f"+${amount:.2f} {wallet.currency} {spec.message_suffix}",
        "action_url": reverse("wallet:student_wallet"),
    }
    target_user_id = wallet.user_id
    transaction.on_commit(
        lambda p=payload, uid=target_user_id: broadcast_wallet_balance_updated(p, uid)
    )
