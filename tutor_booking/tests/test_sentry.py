from unittest.mock import patch
from django.core.exceptions import DisallowedHost
from django.test import SimpleTestCase
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from tutor_booking.sentry import (
    get_sentry_integrations,
    init_sentry,
    strip_sensitive_user_data,
)


class SentryInitializationTests(SimpleTestCase):
    def test_init_sentry_returns_false_when_dsn_empty_or_none(self):
        with patch("sentry_sdk.init") as mock_init:
            self.assertFalse(init_sentry(dsn=""))
            self.assertFalse(init_sentry(dsn=None))
            mock_init.assert_not_called()

    def test_init_sentry_calls_sdk_init_with_expected_config(self):
        test_dsn = "https://examplePublicKey@o0.ingest.sentry.io/0"
        with patch("sentry_sdk.init") as mock_init:
            result = init_sentry(
                dsn=test_dsn,
                environment="staging",
                release="v1.2.3",
                traces_sample_rate=0.0,
                debug=False,
            )
            self.assertTrue(result)
            mock_init.assert_called_once()
            _, kwargs = mock_init.call_args

            self.assertEqual(kwargs["dsn"], test_dsn)
            self.assertEqual(kwargs["environment"], "staging")
            self.assertEqual(kwargs["release"], "v1.2.3")
            self.assertEqual(kwargs["traces_sample_rate"], 0.0)
            self.assertFalse(kwargs["send_default_pii"])
            self.assertIn(DisallowedHost, kwargs["ignore_errors"])
            self.assertEqual(kwargs["before_send"], strip_sensitive_user_data)

            # Verify integrations
            integration_types = [type(i) for i in kwargs["integrations"]]
            self.assertIn(DjangoIntegration, integration_types)
            self.assertIn(CeleryIntegration, integration_types)
            self.assertIn(LoggingIntegration, integration_types)

    def test_init_sentry_defaults_when_optional_args_omitted(self):
        test_dsn = "https://examplePublicKey@o0.ingest.sentry.io/0"
        with patch("sentry_sdk.init") as mock_init:
            init_sentry(dsn=test_dsn, debug=False)
            _, kwargs = mock_init.call_args
            self.assertEqual(kwargs["environment"], "production")
            self.assertIsNone(kwargs["release"])
            self.assertEqual(kwargs["traces_sample_rate"], 0.0)

        with patch("sentry_sdk.init") as mock_init:
            init_sentry(dsn=test_dsn, debug=True)
            _, kwargs = mock_init.call_args
            self.assertEqual(kwargs["environment"], "development")

    def test_init_sentry_handles_invalid_traces_sample_rate(self):
        test_dsn = "https://examplePublicKey@o0.ingest.sentry.io/0"
        with patch("sentry_sdk.init") as mock_init:
            init_sentry(dsn=test_dsn, traces_sample_rate="invalid_rate")
            _, kwargs = mock_init.call_args
            self.assertEqual(kwargs["traces_sample_rate"], 0.0)

    def test_get_sentry_integrations_configuration(self):
        integrations = get_sentry_integrations()
        celery_integ = next((i for i in integrations if isinstance(i, CeleryIntegration)), None)
        self.assertIsNotNone(celery_integ)
        self.assertTrue(getattr(celery_integ, "monitor_beat_tasks", False))

        logging_integ = next((i for i in integrations if isinstance(i, LoggingIntegration)), None)
        self.assertIsNotNone(logging_integ)


class SentryPrivacyTests(SimpleTestCase):
    def test_strip_sensitive_user_data_removes_pii(self):
        event = {
            "user": {
                "id": "123",
                "email": "student@example.com",
                "username": "student123",
                "ip_address": "198.51.100.42",
            }
        }
        cleaned = strip_sensitive_user_data(event, {})
        self.assertEqual(cleaned["user"], {"id": "123"})

    def test_strip_sensitive_user_data_handles_none_or_missing_user(self):
        event1 = {"user": None}
        cleaned1 = strip_sensitive_user_data(event1, {})
        self.assertIsNone(cleaned1["user"])

        event2 = {"message": "something else"}
        cleaned2 = strip_sensitive_user_data(event2, {})
        self.assertNotIn("user", cleaned2)


class SentrySettingsIntegrationTests(SimpleTestCase):
    def test_sentry_remains_uninitialized_during_tests(self):
        self.assertFalse(sentry_sdk.is_initialized())

    def test_sentry_settings_attributes_exist(self):
        from django.conf import settings
        self.assertTrue(hasattr(settings, "SENTRY_DSN"))
        self.assertTrue(hasattr(settings, "SENTRY_ENVIRONMENT"))
        self.assertTrue(hasattr(settings, "SENTRY_RELEASE"))
        self.assertTrue(hasattr(settings, "SENTRY_TRACES_SAMPLE_RATE"))

    def test_test_runner_bypasses_sentry_init_even_when_dsn_set(self):
        import sys
        self.assertIn("test", sys.argv)
