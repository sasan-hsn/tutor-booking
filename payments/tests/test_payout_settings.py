from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from wallet.exceptions import InsufficientBalanceError, WalletError


class PayoutSettingsAndExceptionsTests(SimpleTestCase):
    def test_payout_settings_defaults(self):
        self.assertEqual(getattr(settings, "PAYOUT_CONFIRMATION_HOURS", None), 24)
        self.assertEqual(getattr(settings, "PAYOUT_REMINDER_HOURS", None), 48)

    def test_celery_beat_schedule_registered_tasks(self):
        schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
        self.assertIn("expire-payout-tokens-hourly", schedule)
        self.assertEqual(
            schedule["expire-payout-tokens-hourly"]["task"],
            "payments.tasks.expire_payout_tokens_task",
        )
        self.assertEqual(
            schedule["expire-payout-tokens-hourly"]["schedule"],
            3600.0,
        )

        self.assertIn("expire-orders-hourly", schedule)
        self.assertEqual(
            schedule["expire-orders-hourly"]["task"],
            "payments.tasks.expire_orders_task",
        )
        self.assertEqual(
            schedule["expire-orders-hourly"]["schedule"],
            3600.0,
        )

        self.assertIn("payout-reminders-every-6-hours", schedule)
        self.assertEqual(
            schedule["payout-reminders-every-6-hours"]["task"],
            "payments.tasks.payout_reminder_task",
        )
        self.assertEqual(
            schedule["payout-reminders-every-6-hours"]["schedule"],
            21600.0,
        )

    def test_payout_exceptions_hierarchy(self):
        from payments.exceptions import (
            PayoutConflictError,
            PayoutError,
            PayoutInsufficientBalanceError,
        )

        self.assertTrue(issubclass(PayoutError, WalletError))
        self.assertTrue(issubclass(PayoutConflictError, PayoutError))
        self.assertEqual(PayoutConflictError.status_code, 409)

        # Insufficient balance error should inherit from both InsufficientBalanceError and ValidationError
        self.assertTrue(
            issubclass(PayoutInsufficientBalanceError, InsufficientBalanceError)
        )
        self.assertTrue(issubclass(PayoutInsufficientBalanceError, ValidationError))
