import json
from channels.generic.websocket import AsyncWebsocketConsumer


class HealthCheckConsumer(AsyncWebsocketConsumer):
    """
    Lightweight WebSocket consumer to verify bidirectional frame transmission
    over ASGI and channels infrastructure.
    """

    async def connect(self):
        await self.accept()

    async def receive(self, text_data=None, bytes_data=None):
        if text_data is None:
            return

        if text_data == "ping":
            await self.send(text_data="pong")
            return

        try:
            payload = json.loads(text_data)
            if isinstance(payload, dict) and payload.get("type") == "ping":
                await self.send(text_data=json.dumps({"type": "pong", "status": "ok"}))
        except (json.JSONDecodeError, TypeError):
            pass
