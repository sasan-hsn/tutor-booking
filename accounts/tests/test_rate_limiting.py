import time
from unittest.mock import MagicMock
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from accounts.rate_limiting import (
    check_resend_rate_limit,
    get_client_ip,
    record_resend_attempt,
)

User = get_user_model()


class RateLimitingUnitTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def tearDown(self):
        super().tearDown()
        cache.clear()

    def test_get_client_ip_direct(self):
        request = MagicMock()
        request.META = {'REMOTE_ADDR': '192.168.1.50'}
        self.assertEqual(get_client_ip(request), '192.168.1.50')

    def test_get_client_ip_forwarded_for(self):
        request = MagicMock()
        request.META = {
            'HTTP_X_FORWARDED_FOR': '203.0.113.195, 70.41.3.18, 150.172.238.178',
            'REMOTE_ADDR': '127.0.0.1',
        }
        self.assertEqual(get_client_ip(request), '203.0.113.195')

    def test_first_request_permitted(self):
        allowed, reason, retry_after = check_resend_rate_limit(ip='10.0.0.1')
        self.assertTrue(allowed)
        self.assertIsNone(reason)
        self.assertEqual(retry_after, 0)

    def test_cooldown_blocks_immediate_retry(self):
        record_resend_attempt(ip='10.0.0.1', cooldown_seconds=60)
        allowed, reason, retry_after = check_resend_rate_limit(ip='10.0.0.1', cooldown_seconds=60)

        self.assertFalse(allowed)
        self.assertEqual(reason, 'cooldown')
        self.assertGreater(retry_after, 0)
        self.assertLessEqual(retry_after, 60)

    def test_hourly_ceiling_blocks_sixth_attempt(self):
        ip = '10.0.0.2'
        for i in range(5):
            allowed, _, _ = check_resend_rate_limit(ip=ip, cooldown_seconds=0, hourly_limit=5)
            self.assertTrue(allowed, f"Attempt {i+1} should be allowed")
            record_resend_attempt(ip=ip, cooldown_seconds=0)

        # 6th attempt should be blocked by ceiling
        allowed, reason, retry_after = check_resend_rate_limit(ip=ip, cooldown_seconds=0, hourly_limit=5)
        self.assertFalse(allowed)
        self.assertEqual(reason, 'ceiling')
        self.assertGreater(retry_after, 0)
        self.assertLessEqual(retry_after, 3600)

    def test_user_and_ip_isolation(self):
        user_mock1 = MagicMock(pk=101)
        user_mock2 = MagicMock(pk=102)

        record_resend_attempt(user=user_mock1, ip='10.0.0.1')

        # user1 and ip 10.0.0.1 are blocked
        self.assertFalse(check_resend_rate_limit(user=user_mock1)[0])
        self.assertFalse(check_resend_rate_limit(ip='10.0.0.1')[0])

        # user2 with different IP is permitted
        self.assertTrue(check_resend_rate_limit(user=user_mock2, ip='10.0.0.2')[0])
