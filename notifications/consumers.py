import asyncio
import logging
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.exceptions import StopConsumer
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from channels.utils import await_many_dispatch

try:
    from redis.exceptions import TimeoutError as RedisTimeoutError, RedisError
except ImportError:
    class RedisTimeoutError(Exception):
        pass

    class RedisError(Exception):
        pass

from .models import Notification

logger = logging.getLogger(__name__)


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for streaming real-time notifications to authenticated users.
    Rejects unauthenticated sessions during handshake.
    Resilient to Redis connection and channel layer timeouts.
    """

    async def __call__(self, scope, receive, send):
        """
        Dispatches incoming messages to type-based handlers asynchronously.
        Wraps channel layer receive in resilient retry logic so that Redis stream
        timeouts or transient connection drops do not abort the active WebSocket connection.
        """
        self.scope = scope

        self.channel_layer = get_channel_layer(self.channel_layer_alias)
        if self.channel_layer is not None:
            self.channel_name = await self.channel_layer.new_channel()

            async def resilient_channel_receive():
                while True:
                    try:
                        return await self.channel_layer.receive(self.channel_name)
                    except (TimeoutError, asyncio.TimeoutError, RedisTimeoutError) as e:
                        logger.debug("Channel layer read timeout; resuming listener: %s", e)
                        await asyncio.sleep(0.2)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning("Channel layer read error; resuming listener: %s", e)
                        await asyncio.sleep(1.0)

            self.channel_receive = resilient_channel_receive

        if self._sync:
            self.base_send = async_to_sync(send)
        else:
            self.base_send = send

        try:
            if self.channel_layer is not None:
                await await_many_dispatch(
                    [receive, self.channel_receive], self.dispatch
                )
            else:
                await await_many_dispatch([receive], self.dispatch)
        except StopConsumer:
            pass
        except (TimeoutError, asyncio.TimeoutError, RedisTimeoutError) as e:
            logger.warning("Consumer dispatch loop caught timeout: %s", e)
        except asyncio.CancelledError:
            pass

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close()
            return

        # Complete WebSocket handshake before external channel layer operations
        # so delays or timeouts in Redis do not abort the handshake.
        await self.accept()

        self.group_name = Notification.get_group_name(user.id)
        if self.channel_layer:
            try:
                await asyncio.wait_for(
                    self.channel_layer.group_add(self.group_name, self.channel_name),
                    timeout=2.0,
                )
            except (TimeoutError, asyncio.TimeoutError, RedisTimeoutError, RedisError, Exception) as e:
                logger.warning(
                    "Channel layer group_add failed or timed out for user #%s: %s",
                    user.id,
                    e,
                )

    async def disconnect(self, close_code):
        if hasattr(self, "group_name") and self.group_name and self.channel_layer:
            try:
                await asyncio.wait_for(
                    self.channel_layer.group_discard(self.group_name, self.channel_name),
                    timeout=2.0,
                )
            except (TimeoutError, asyncio.TimeoutError, RedisTimeoutError, RedisError, Exception) as e:
                logger.warning(
                    "Channel layer group_discard failed or timed out for group %s: %s",
                    self.group_name,
                    e,
                )

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            return

        action = content.get("action")
        if action == "get_unread_count":
            user = self.scope.get("user")
            if user and user.is_authenticated:
                count = await self._get_unread_count(user.id)
                await self.send_json({"type": "unread_count", "count": count})
        elif action == "get_unread_chat_count":
            user = self.scope.get("user")
            if user and user.is_authenticated:
                count = await self._get_unread_chat_count(user.id)
                await self.send_json({"type": "unread_chat_count", "count": count})

    async def notification_message(self, event):
        """
        Handler invoked when a message with type='notification_message'
        is broadcast to the user's private channel layer group.
        """
        await self.send_json(event)

    async def notification_read(self, event):
        """
        Handler invoked when a message with type='notification_read'
        is broadcast to the user's private channel layer group.
        """
        await self.send_json(event)

    async def unread_chat_count(self, event):
        """
        Handler invoked when an unread chat count update frame is broadcast
        to the user's private notification channel layer group.
        """
        await self.send_json(event)

    @database_sync_to_async
    def _get_unread_count(self, user_id):
        return Notification.objects.unread_count_for(user_id)

    @database_sync_to_async
    def _get_unread_chat_count(self, user_id):
        from chat.services import get_total_unread_chat_count
        return get_total_unread_chat_count(user_id)
