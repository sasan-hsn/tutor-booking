import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

PRESENCE_KEY_TEMPLATE = "chat_presence:{conversation_id}:{user_id}"
PRESENCE_TIMEOUT = 86400  # 24-hour safety TTL for ephemeral connection tracking


def get_presence_key(conversation_id, user_id) -> str:
    return PRESENCE_KEY_TEMPLATE.format(
        conversation_id=conversation_id, user_id=user_id
    )


def add_room_presence(conversation_id, user_id, channel_name: str) -> None:
    """
    Registers an active WebSocket connection channel for a user in a conversation room.
    """
    try:
        key = get_presence_key(conversation_id, user_id)
        current = cache.get(key)
        if current is None:
            channels = {channel_name}
        elif isinstance(current, (set, list, tuple)):
            channels = set(current)
            channels.add(channel_name)
        else:
            channels = {channel_name}
        cache.set(key, channels, timeout=PRESENCE_TIMEOUT)
    except Exception as exc:
        logger.warning(
            "Failed to add room presence for user %s in conversation %s: %s",
            user_id,
            conversation_id,
            exc,
        )


def remove_room_presence(conversation_id, user_id, channel_name: str) -> None:
    """
    Unregisters a WebSocket connection channel for a user in a conversation room.
    Removes the key if no remaining active channels exist.
    """
    try:
        key = get_presence_key(conversation_id, user_id)
        current = cache.get(key)
        if current is None:
            return
        if isinstance(current, (set, list, tuple)):
            channels = set(current)
            channels.discard(channel_name)
            if channels:
                cache.set(key, channels, timeout=PRESENCE_TIMEOUT)
            else:
                cache.delete(key)
        else:
            cache.delete(key)
    except Exception as exc:
        logger.warning(
            "Failed to remove room presence for user %s in conversation %s: %s",
            user_id,
            conversation_id,
            exc,
        )


def is_user_in_room(conversation_id, user_id) -> bool:
    """
    Checks whether a participant currently maintains at least one active
    WebSocket connection in the conversation room.
    """
    try:
        key = get_presence_key(conversation_id, user_id)
        channels = cache.get(key)
        return bool(channels)
    except Exception as exc:
        logger.warning(
            "Failed to check room presence for user %s in conversation %s: %s",
            user_id,
            conversation_id,
            exc,
        )
        return False
