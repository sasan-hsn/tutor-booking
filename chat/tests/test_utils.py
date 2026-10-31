import time
from unittest.mock import patch
from django.core.cache import cache
from django.test import TestCase, override_settings

from chat.utils import (
    CHAT_RATE_LIMIT,
    CHAT_RATE_WINDOW_SECONDS,
    check_chat_rate_limit,
    linkify_message_text,
    record_chat_message,
)


class LinkifyMessageTextTests(TestCase):
    def test_plain_text_without_urls(self):
        text = "Hello world! How are you?"
        result = linkify_message_text(text)
        self.assertEqual(result, "Hello world! How are you?")

    def test_linkify_https_and_http_urls(self):
        text = "Visit https://example.com and http://test.org/page?query=1&var=2 today!"
        result = linkify_message_text(text)
        expected = (
            'Visit <a href="https://example.com" target="_blank" rel="noopener noreferrer">https://example.com</a> '
            'and <a href="http://test.org/page?query=1&amp;var=2" target="_blank" rel="noopener noreferrer">http://test.org/page?query=1&amp;var=2</a> today!'
        )
        self.assertEqual(result, expected)

    def test_reject_dangerous_schemes(self):
        text = "javascript:alert(1) data:text/html,bad file:///etc/passwd"
        result = linkify_message_text(text)
        self.assertNotIn("<a ", result)
        self.assertIn("javascript:alert(1)", result)

    def test_html_escaping_and_xss_protection(self):
        text = "<script>alert('xss')</script> <b>bold</b> https://safe.com/test?a=1&b=2"
        result = linkify_message_text(text)
        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", result)
        self.assertNotIn("<b>", result)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", result)
        self.assertIn('<a href="https://safe.com/test?a=1&amp;b=2" target="_blank" rel="noopener noreferrer">https://safe.com/test?a=1&amp;b=2</a>', result)

    def test_urls_with_trailing_punctuation(self):
        text = "Check this out (https://example.com/item). Wow, look at https://example.org/test!"
        result = linkify_message_text(text)
        self.assertIn('href="https://example.com/item"', result)
        self.assertTrue(result.endswith("!"))
        self.assertIn("(<a href=", result)


class ChatRateLimitingTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_sliding_window_allows_up_to_limit(self):
        user_id = 999
        ip = "192.168.1.1"

        for i in range(CHAT_RATE_LIMIT):
            allowed, remaining = check_chat_rate_limit(user_id=user_id, ip=ip)
            self.assertTrue(allowed, f"Message {i+1} should be allowed")
            record_chat_message(user_id=user_id, ip=ip)

        # 31st message should be blocked
        allowed, remaining = check_chat_rate_limit(user_id=user_id, ip=ip)
        self.assertFalse(allowed)
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, CHAT_RATE_WINDOW_SECONDS)

    def test_sliding_window_expires(self):
        user_id = 888
        ip = "192.168.1.2"

        # Record messages at T - 65 seconds
        past_time = time.time() - 65
        with patch("chat.utils.time.time", return_value=past_time):
            for _ in range(CHAT_RATE_LIMIT):
                record_chat_message(user_id=user_id, ip=ip)

        # At current time, limit should not be exceeded
        allowed, remaining = check_chat_rate_limit(user_id=user_id, ip=ip)
        self.assertTrue(allowed)
        self.assertEqual(remaining, 0)

    def test_rate_limit_fails_open_on_cache_error(self):
        with patch("chat.utils.cache.get", side_effect=Exception("Redis down")):
            allowed, remaining = check_chat_rate_limit(user_id=1, ip="1.2.3.4")
            self.assertTrue(allowed)
            self.assertEqual(remaining, 0)
