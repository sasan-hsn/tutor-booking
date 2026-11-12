from django.urls import path

from chat.routing import websocket_urlpatterns as chat_websocket_urlpatterns
from notifications.routing import (
    websocket_urlpatterns as notifications_websocket_urlpatterns,
)

from . import consumers

websocket_urlpatterns = [
    path("ws/health/", consumers.HealthCheckConsumer.as_asgi()),
    *notifications_websocket_urlpatterns,
    *chat_websocket_urlpatterns,
]
