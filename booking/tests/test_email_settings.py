import os
from unittest.mock import patch

from django.conf import settings
from django.core.mail import get_connection
from django.test import SimpleTestCase

import anymail.backends.brevo


class EmailSettingsTests(SimpleTestCase):
    """Tests for Anymail Brevo configuration and backend fallback logic."""

    def test_anymail_installed_in_apps(self):
        """Verify anymail is registered in INSTALLED_APPS."""
        self.assertIn('anymail', settings.INSTALLED_APPS)

    def test_server_email_configured(self):
        """Verify SERVER_EMAIL is configured to match DEFAULT_FROM_EMAIL."""
        self.assertEqual(settings.SERVER_EMAIL, settings.DEFAULT_FROM_EMAIL)

    def test_anymail_brevo_backend_can_instantiate(self):
        """Verify anymail.backends.brevo.EmailBackend can be instantiated."""
        conn = get_connection('anymail.backends.brevo.EmailBackend', api_key='dummy-key')
        self.assertIsInstance(conn, anymail.backends.brevo.EmailBackend)

    def _resolve_backend(self, env_vars):
        """Helper to simulate settings.py email backend selection logic."""
        debug = env_vars.get('DEBUG', 'False') == 'True'
        brevo_api_key = env_vars.get('BREVO_API_KEY', '')

        if debug:
            default_backend = 'django.core.mail.backends.console.EmailBackend'
        elif brevo_api_key:
            default_backend = 'anymail.backends.brevo.EmailBackend'
        else:
            default_backend = 'django.core.mail.backends.smtp.EmailBackend'

        return env_vars.get('EMAIL_BACKEND', default_backend)

    def test_local_dev_defaults_to_console_backend(self):
        """When DEBUG=True and no API key, default backend is console."""
        backend = self._resolve_backend({'DEBUG': 'True', 'BREVO_API_KEY': ''})
        self.assertEqual(backend, 'django.core.mail.backends.console.EmailBackend')

    def test_local_dev_defaults_to_console_even_with_brevo_key(self):
        """When DEBUG=True, default backend is console to avoid accidental live sends in dev."""
        backend = self._resolve_backend({'DEBUG': 'True', 'BREVO_API_KEY': 'some-key'})
        self.assertEqual(backend, 'django.core.mail.backends.console.EmailBackend')

    def test_production_uses_brevo_backend_when_api_key_set(self):
        """When DEBUG=False and BREVO_API_KEY is set, default backend is Brevo Anymail."""
        backend = self._resolve_backend({'DEBUG': 'False', 'BREVO_API_KEY': 'real-brevo-api-key'})
        self.assertEqual(backend, 'anymail.backends.brevo.EmailBackend')

    def test_production_falls_back_to_smtp_when_no_brevo_key(self):
        """When DEBUG=False and no BREVO_API_KEY, fallback backend is standard SMTP."""
        backend = self._resolve_backend({'DEBUG': 'False', 'BREVO_API_KEY': ''})
        self.assertEqual(backend, 'django.core.mail.backends.smtp.EmailBackend')

    def test_explicit_email_backend_env_var_takes_precedence(self):
        """Explicit EMAIL_BACKEND in env always overrides default selection."""
        backend = self._resolve_backend({
            'DEBUG': 'True',
            'BREVO_API_KEY': 'some-key',
            'EMAIL_BACKEND': 'django.core.mail.backends.dummy.EmailBackend',
        })
        self.assertEqual(backend, 'django.core.mail.backends.dummy.EmailBackend')
