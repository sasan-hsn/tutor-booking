import asyncio
import logging
import time
import uuid
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

from .models import Conversation
from .services import advance_watermark

logger = logging.getLogger(__name__)


class ChatConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for real-time 1-on-1 chat delivery, read receipts,
    and ephemeral typing indicators.
    Rejects unauthenticated or unauthorized users at handshake.
    """

    async def __call__(self, scope, receive, send):
        self.scope = scope

        self.channel_layer = get_channel_layer(self.channel_layer_alias)
        if self.channel_layer is not None:
            self.channel_name = await self.channel_layer.new_channel()

            async def resilient_channel_receive():
                while True:
                    try:
                        return await self.channel_layer.receive(self.channel_name)
                    except (TimeoutError, asyncio.TimeoutError, RedisTimeoutError) as e:
                        logger.debug("Chat channel layer read timeout; resuming listener: %s", e)
                        await asyncio.sleep(0.2)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning("Chat channel layer read error; resuming listener: %s", e)
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
            logger.warning("Chat consumer dispatch loop caught timeout: %s", e)
        except asyncio.CancelledError:
            pass

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close()
            return

        if getattr(user, "role", None) not in ("student", "teacher"):
            await self.close()
            return

        raw_conv_id = self.scope.get("url_route", {}).get("kwargs", {}).get("conversation_id")
        if not raw_conv_id:
            await self.close()
            return

        try:
            conv_uuid = uuid.UUID(str(raw_conv_id))
        except (ValueError, TypeError):
            await self.close()
            return

        conv = await self._get_verified_conversation(conv_uuid, user)
        if not conv:
            await self.close()
            return

        self.user = user
        self.conversation_id = conv_uuid
        self.conversation = conv
        self.group_name = Conversation.get_group_name(self.conversation_id)
        self.last_typing_time = 0.0

        await self.accept()

        if self.channel_layer:
            try:
                await asyncio.wait_for(
                    self.channel_layer.group_add(self.group_name, self.channel_name),
                    timeout=2.0,
                )
            except (TimeoutError, asyncio.TimeoutError, RedisTimeoutError, RedisError, Exception) as e:
                logger.warning(
                    "Channel layer group_add failed or timed out for conversation %s: %s",
                    self.conversation_id,
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
                    "Channel layer group_discard failed for conversation %s: %s",
                    self.group_name,
                    e,
                )

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            return

        action = content.get("action")

        if action == "advance_watermark":
            message_id = content.get("message_id")
            if isinstance(message_id, int) and message_id > 0:
                current_watermark = await self._advance_watermark_in_db(message_id)
                if self.channel_layer and current_watermark > 0:
                    await self.channel_layer.group_send(
                        self.group_name,
                        {
                            "type": "chat_read_receipt",
                            "reader_id": self.user.id,
                            "watermark": current_watermark,
                        },
                    )

        elif action == "typing":
            now = time.monotonic()
            if now - self.last_typing_time >= 3.0:
                self.last_typing_time = now
                if self.channel_layer:
                    await self.channel_layer.group_send(
                        self.group_name,
                        {
                            "type": "chat_user_typing",
                            "user_id": self.user.id,
                            "username": self.user.get_full_name() or self.user.username,
                        },
                    )

    async def chat_message(self, event):
        """
        Handler invoked when a new message payload is broadcast to the conversation group.
        """
        await self.send_json({
            "type": "new_message",
            "message": event["message"],
        })

    async def chat_read_receipt(self, event):
        """
        Handler invoked when a read receipt frame is broadcast to the conversation group.
        """
        await self.send_json({
            "type": "read_receipt",
            "reader_id": event["reader_id"],
            "watermark": event["watermark"],
        })

    async def chat_user_typing(self, event):
        """
        Handler invoked when an ephemeral typing indicator is broadcast to the conversation group.
        """
        await self.send_json({
            "type": "user_typing",
            "user_id": event["user_id"],
            "username": event["username"],
        })

    @database_sync_to_async
    def _get_verified_conversation(self, conv_uuid, user):
        try:
            conv = Conversation.objects.select_related("student", "teacher").get(id=conv_uuid)
            if user.id in (conv.student_id, conv.teacher_id):
                return conv
            return None
        except Conversation.DoesNotExist:
            return None

    @database_sync_to_async
    def _advance_watermark_in_db(self, message_id):
        return advance_watermark(self.conversation, self.user, message_id)
