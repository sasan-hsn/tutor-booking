import time
from unittest.mock import MagicMock
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from accounts.rate_limiting import (
    LOCKOUT_TYPE_COMPOUND,
    LOCKOUT_TYPE_IP_CEILING,
    check_login_ip_rate_limit,
    check_login_rate_limit,
    check_resend_rate_limit,
    get_client_ip,
    normalize_username,
    record_login_failure,
    record_resend_attempt,
    reset_login_ip_rate_limit,
    reset_login_rate_limit,
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

    def test_normalize_username(self):
        self.assertEqual(normalize_username('  alice  '), 'alice')
        self.assertEqual(normalize_username('Alice'), 'alice')
        self.assertEqual(normalize_username('BOB'), 'bob')
        self.assertEqual(normalize_username('  User.Test@Example.com '), 'user.test@example.com')
        self.assertEqual(normalize_username(''), '')
        self.assertEqual(normalize_username(None), '')
        self.assertEqual(normalize_username('   '), '')

    def test_login_rate_limit_initial_allowed(self):
        allowed, retry_after = check_login_rate_limit(ip='10.0.0.1', username='alice')
        self.assertTrue(allowed)
        self.assertEqual(retry_after, 0)

    def test_login_rate_limit_locks_out_after_five_failures(self):
        ip = '10.0.0.1'
        username = 'student1'

        for i in range(5):
            allowed, retry_after = check_login_rate_limit(ip=ip, username=username)
            self.assertTrue(allowed, f"Attempt {i+1} should be allowed before failure is recorded")
            self.assertEqual(retry_after, 0)
            record_login_failure(ip=ip, username=username)

        # 6th attempt is locked out
        allowed, retry_after = check_login_rate_limit(ip=ip, username=username)
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)
        self.assertLessEqual(retry_after, 300)

    def test_login_rate_limit_username_normalization(self):
        ip = '10.0.0.1'
        for _ in range(4):
            record_login_failure(ip=ip, username='  Alice  ')
        record_login_failure(ip=ip, username='ALICE')

        # 'alice' should now be locked out
        allowed, retry_after = check_login_rate_limit(ip=ip, username='alice')
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)

    def test_login_rate_limit_sliding_window_expiration(self):
        ip = '10.0.0.1'
        username = 'alice'
        now = 1000.0

        key = f"rl:login:user_ip:{ip}:{username}"
        # 4 attempts at t=700 (300s ago relative to now=1000), 1 attempt at t=900 (100s ago)
        cache.set(key, [699.0, 699.0, 699.0, 699.0, 900.0], timeout=300)

        # At now=1000.0, the 4 attempts at 699.0 are expired (1000 - 699 = 301 > 300).
        # Only 1 valid attempt remains (< 5 limit), so it should be allowed.
        from unittest.mock import patch
        with patch('time.time', return_value=now):
            allowed, retry_after = check_login_rate_limit(ip=ip, username=username)
            self.assertTrue(allowed)
            self.assertEqual(retry_after, 0)

    def test_login_rate_limit_passive_rejection_does_not_extend_window(self):
        ip = '10.0.0.1'
        username = 'alice'

        from unittest.mock import patch
        with patch('time.time', return_value=1000.0):
            for _ in range(5):
                record_login_failure(ip=ip, username=username)

        # Check at t=1050 (50s into the 300s window -> ~250s remaining)
        with patch('time.time', return_value=1050.0):
            allowed, retry_after_1 = check_login_rate_limit(ip=ip, username=username)
            self.assertFalse(allowed)
            self.assertEqual(retry_after_1, 250)

        # Check again at t=1100 (100s into window -> ~200s remaining)
        # Verify passive checking did NOT extend the cooldown back to 300s
        with patch('time.time', return_value=1100.0):
            allowed, retry_after_2 = check_login_rate_limit(ip=ip, username=username)
            self.assertFalse(allowed)
            self.assertEqual(retry_after_2, 200)

    def test_login_rate_limit_reset_on_success(self):
        ip = '10.0.0.1'
        username = 'alice'

        for _ in range(5):
            record_login_failure(ip=ip, username=username)

        allowed, _ = check_login_rate_limit(ip=ip, username=username)
        self.assertFalse(allowed)

        reset_login_rate_limit(ip=ip, username='  ALICE  ')

        allowed, retry_after = check_login_rate_limit(ip=ip, username=username)
        self.assertTrue(allowed)
        self.assertEqual(retry_after, 0)

    def test_login_rate_limit_ip_isolation(self):
        username = 'alice'
        for _ in range(5):
            record_login_failure(ip='10.0.0.1', username=username)

        # IP 10.0.0.1 is locked out
        self.assertFalse(check_login_rate_limit(ip='10.0.0.1', username=username)[0])

        # IP 10.0.0.2 is NOT locked out
        allowed, retry_after = check_login_rate_limit(ip='10.0.0.2', username=username)
        self.assertTrue(allowed)
        self.assertEqual(retry_after, 0)

    def test_login_rate_limit_account_isolation(self):
        ip = '10.0.0.1'
        for _ in range(5):
            record_login_failure(ip=ip, username='alice')

        # Alice is locked out on IP 10.0.0.1
        self.assertFalse(check_login_rate_limit(ip=ip, username='alice')[0])

        # Bob is NOT locked out on IP 10.0.0.1
        allowed, retry_after = check_login_rate_limit(ip=ip, username='bob')
        self.assertTrue(allowed)
        self.assertEqual(retry_after, 0)

    def test_login_rate_limit_empty_or_none_inputs(self):
        self.assertEqual(check_login_rate_limit(None, 'alice'), (True, 0))
        self.assertEqual(check_login_rate_limit('10.0.0.1', None), (True, 0))
        self.assertEqual(check_login_rate_limit('10.0.0.1', '   '), (True, 0))

        # None of these should raise errors or populate cache
        record_login_failure(None, 'alice')
        record_login_failure('10.0.0.1', None)
        record_login_failure('10.0.0.1', '   ')
        reset_login_rate_limit(None, 'alice')
        reset_login_rate_limit('10.0.0.1', None)

    def test_cache_failure_fails_open_for_login(self):
        from unittest.mock import patch
        with patch.object(cache, 'get', side_effect=Exception("Redis connection error")):
            allowed, retry_after = check_login_rate_limit('10.0.0.1', 'alice')
            self.assertTrue(allowed)
            self.assertEqual(retry_after, 0)

        with patch.object(cache, 'set', side_effect=Exception("Redis write error")):
            # Should not raise exception
            record_login_failure('10.0.0.1', 'alice')

        with patch.object(cache, 'delete', side_effect=Exception("Redis delete error")):
            # Should not raise exception
            reset_login_rate_limit('10.0.0.1', 'alice')

    def test_cache_failure_fails_open_for_resend(self):
        from unittest.mock import patch
        with patch.object(cache, 'get', side_effect=Exception("Redis connection error")):
            allowed, reason, retry_after = check_resend_rate_limit(ip='10.0.0.1')
            self.assertTrue(allowed)
            self.assertIsNone(reason)
            self.assertEqual(retry_after, 0)

        with patch.object(cache, 'set', side_effect=Exception("Redis write error")):
            # Should not raise exception
            record_resend_attempt(ip='10.0.0.1')

    def test_login_ip_rate_limit_initial_allowed(self):
        allowed, retry_after = check_login_ip_rate_limit(ip='10.0.0.1')
        self.assertTrue(allowed)
        self.assertEqual(retry_after, 0)

    def test_login_ip_rate_limit_locks_out_after_twenty_failures(self):
        ip = '10.0.0.1'
        for i in range(20):
            allowed, retry_after = check_login_ip_rate_limit(ip=ip)
            self.assertTrue(allowed, f"Attempt {i+1} should be allowed before 20 failures are recorded")
            self.assertEqual(retry_after, 0)
            record_login_failure(ip=ip, username=f"user_{i}")

        # 21st attempt from this IP is locked out
        allowed, retry_after = check_login_ip_rate_limit(ip=ip)
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)
        self.assertLessEqual(retry_after, 900)

    def test_check_login_rate_limit_enforces_ip_ceiling_across_distinct_users(self):
        ip = '10.0.0.1'
        for i in range(20):
            record_login_failure(ip=ip, username=f"user_{i}")

        # Attempt for a completely fresh user21 from the same IP is blocked
        allowed, retry_after = check_login_rate_limit(ip=ip, username='user_21')
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)
        self.assertLessEqual(retry_after, 900)

    def test_login_ip_rate_limit_sliding_window_expiration(self):
        ip = '10.0.0.1'
        now = 2000.0
        key = f"rl:login:ip:{ip}"
        # 20 attempts recorded at t=1099.0 (relative to now=2000.0, 2000 - 1099 = 901 > 900)
        cache.set(key, [1099.0] * 20, timeout=900)

        from unittest.mock import patch
        with patch('time.time', return_value=now):
            allowed, retry_after = check_login_ip_rate_limit(ip=ip)
            self.assertTrue(allowed)
            self.assertEqual(retry_after, 0)

    def test_login_ip_rate_limit_passive_rejection_does_not_extend_window(self):
        ip = '10.0.0.1'
        from unittest.mock import patch
        with patch('time.time', return_value=1000.0):
            for i in range(20):
                record_login_failure(ip=ip, username=f"user_{i}")

        # At t=1300 (300s into 900s window -> 600s remaining)
        with patch('time.time', return_value=1300.0):
            allowed, retry_after_1 = check_login_ip_rate_limit(ip=ip)
            self.assertFalse(allowed)
            self.assertEqual(retry_after_1, 600)

        # At t=1500 (500s into 900s window -> 400s remaining)
        with patch('time.time', return_value=1500.0):
            allowed, retry_after_2 = check_login_ip_rate_limit(ip=ip)
            self.assertFalse(allowed)
            self.assertEqual(retry_after_2, 400)

    def test_reset_login_ip_rate_limit(self):
        ip = '10.0.0.1'
        for i in range(20):
            record_login_failure(ip=ip, username=f"user_{i}")

        allowed, _ = check_login_ip_rate_limit(ip=ip)
        self.assertFalse(allowed)

        reset_login_ip_rate_limit(ip=ip)

        allowed, retry_after = check_login_ip_rate_limit(ip=ip)
        self.assertTrue(allowed)
        self.assertEqual(retry_after, 0)

    def test_successful_login_resets_compound_but_not_ip_ceiling(self):
        ip = '10.0.0.1'
        for i in range(10):
            record_login_failure(ip=ip, username=f"user_{i}")

        # Reset compound key for user_0
        reset_login_rate_limit(ip=ip, username='user_0')

        # IP ceiling bucket still has 10 attempts
        ip_timestamps = cache.get(f"rl:login:ip:{ip}")
        self.assertEqual(len(ip_timestamps), 10)

    def test_check_login_rate_limit_blocks_without_username_when_ip_ceiling_hit(self):
        ip = '10.0.0.1'
        for i in range(20):
            record_login_failure(ip=ip, username=f"user_{i}")

        # Even with empty/None username, global IP ceiling blocks the attempt
        allowed, retry_after = check_login_rate_limit(ip=ip, username=None)
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)

        allowed_empty, retry_after_empty = check_login_rate_limit(ip=ip, username='   ')
        self.assertFalse(allowed_empty)
        self.assertGreater(retry_after_empty, 0)

    def test_audit_logging_compound_lockout(self):
        ip = '10.0.0.1'
        username = 'student1'
        for _ in range(5):
            record_login_failure(ip=ip, username=username)

        with self.assertLogs('accounts.rate_limiting', level='WARNING') as cm:
            allowed, retry_after = check_login_rate_limit(ip=ip, username=username)

        self.assertFalse(allowed)
        self.assertEqual(len(cm.records), 1)
        record = cm.records[0]
        self.assertEqual(record.client_ip, ip)
        self.assertEqual(record.normalized_username, 'student1')
        self.assertEqual(record.lockout_type, LOCKOUT_TYPE_COMPOUND)
        self.assertEqual(record.retry_after, retry_after)
        self.assertIn(ip, cm.output[0])
        self.assertIn('student1', cm.output[0])
        self.assertIn(LOCKOUT_TYPE_COMPOUND, cm.output[0])
        self.assertNotIn('password', cm.output[0].lower())

    def test_audit_logging_ip_ceiling_lockout(self):
        ip = '10.0.0.1'
        for i in range(20):
            record_login_failure(ip=ip, username=f"user_{i}")

        with self.assertLogs('accounts.rate_limiting', level='WARNING') as cm:
            allowed, retry_after = check_login_rate_limit(ip=ip, username='fresh_user')

        self.assertFalse(allowed)
        self.assertEqual(len(cm.records), 1)
        record = cm.records[0]
        self.assertEqual(record.client_ip, ip)
        self.assertEqual(record.normalized_username, 'fresh_user')
        self.assertEqual(record.lockout_type, LOCKOUT_TYPE_IP_CEILING)
        self.assertEqual(record.retry_after, retry_after)
        self.assertIn(ip, cm.output[0])
        self.assertIn('fresh_user', cm.output[0])
        self.assertIn(LOCKOUT_TYPE_IP_CEILING, cm.output[0])
        self.assertNotIn('password', cm.output[0].lower())



