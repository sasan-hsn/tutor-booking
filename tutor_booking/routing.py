from django.urls import path
from . import consumers

websocket_urlpatterns = [
    path("ws/health/", consumers.HealthCheckConsumer.as_asgi()),
]
