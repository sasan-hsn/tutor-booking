from django.conf import settings
from django.test import SimpleTestCase
from channels.routing import ProtocolTypeRouter
from channels.testing import WebsocketCommunicator


class ASGISettingsTests(SimpleTestCase):
    def test_asgi_application_setting_points_to_tutor_booking_asgi(self):
        self.assertEqual(settings.ASGI_APPLICATION, "tutor_booking.asgi.application")

    def test_channels_in_installed_apps(self):
        self.assertIn("channels", settings.INSTALLED_APPS)

    def test_channel_layers_configured_for_testing(self):
        self.assertIn("default", settings.CHANNEL_LAYERS)
        self.assertEqual(
            settings.CHANNEL_LAYERS["default"]["BACKEND"],
            "channels.layers.InMemoryChannelLayer",
        )

    def test_redis_channel_layer_url_setting_exists(self):
        self.assertTrue(hasattr(settings, "REDIS_CHANNEL_LAYER_URL"))
        self.assertTrue(settings.REDIS_CHANNEL_LAYER_URL.endswith("/2"))


class ASGIRoutingAndHealthCheckTests(SimpleTestCase):
    def test_asgi_application_is_protocol_type_router(self):
        from tutor_booking.asgi import application
        self.assertIsInstance(application, ProtocolTypeRouter)

    async def test_websocket_health_check_ping_pong_text(self):
        from tutor_booking.asgi import application

        communicator = WebsocketCommunicator(application, "/ws/health/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_to(text_data="ping")
        response = await communicator.receive_from()
        self.assertEqual(response, "pong")

        await communicator.disconnect()

    async def test_websocket_health_check_json_frame(self):
        from tutor_booking.asgi import application

        communicator = WebsocketCommunicator(application, "/ws/health/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to({"type": "ping"})
        response = await communicator.receive_json_from()
        self.assertEqual(response, {"type": "pong", "status": "ok"})

        await communicator.disconnect()

    async def test_unknown_websocket_route_raises_routing_error(self):
        from tutor_booking.asgi import application

        communicator = WebsocketCommunicator(application, "/ws/non-existent-route/")
        with self.assertRaises(ValueError):
            await communicator.connect()

    def test_http_health_check_still_works(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")
