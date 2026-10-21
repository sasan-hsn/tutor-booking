import logging
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import Notification

logger = logging.getLogger(__name__)


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for streaming real-time notifications to authenticated users.
    Rejects unauthenticated sessions during handshake.
    """

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close()
            return

        try:
            self.group_name = Notification.get_group_name(user.id)
            if self.channel_layer:
                await self.channel_layer.group_add(self.group_name, self.channel_name)
            await self.accept()
        except Exception:
            logger.exception("Failed to connect notification consumer for user #%s", user.id)
            await self.close()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name") and self.group_name and self.channel_layer:
            try:
                await self.channel_layer.group_discard(self.group_name, self.channel_name)
            except Exception:
                logger.exception("Failed to discard notification group for user on disconnect")

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            return

        action = content.get("action")
        if action == "get_unread_count":
            user = self.scope.get("user")
            if user and user.is_authenticated:
                count = await self._get_unread_count(user.id)
                await self.send_json({"type": "unread_count", "count": count})

    async def notification_message(self, event):
        """
        Handler invoked when a message with type='notification_message'
        is broadcast to the user's private channel layer group.
        """
        await self.send_json(event)

    @database_sync_to_async
    def _get_unread_count(self, user_id):
        return Notification.objects.unread_count_for(user_id)
