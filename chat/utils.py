import logging
import math
import re
import time
from django.core.cache import cache
from django.utils.html import Urlizer, escape, mark_safe
from accounts.rate_limiting import get_client_ip

logger = logging.getLogger(__name__)

CHAT_RATE_LIMIT = 30
CHAT_RATE_WINDOW_SECONDS = 60


class SafeChatUrlizer(Urlizer):
    url_template = '<a href="{href}" target="_blank" rel="noopener noreferrer">{url}</a>'

    def is_email_simple(self, value):
        return False

    def handle_word(
        self,
        word,
        *,
        safe_input,
        trim_url_limit=None,
        nofollow=False,
        autoescape=False,
    ):
        lead, middle, trail = self.trim_punctuation(word)
        if not (middle.startswith("http://") or middle.startswith("https://")):
            if autoescape:
                return escape(word)
            return word

        return super().handle_word(
            word,
            safe_input=safe_input,
            trim_url_limit=trim_url_limit,
            nofollow=nofollow,
            autoescape=autoescape,
        )


_chat_urlizer = SafeChatUrlizer()


def linkify_message_text(text: str) -> str:
    if not text:
        return ""
    return _chat_urlizer(text, autoescape=True)


def _get_rate_limit_keys(user_id=None, ip: str | None = None) -> list[str]:
    keys = []
    if user_id:
        keys.append(f"rl:chat:user:{user_id}")
    if ip and ip.strip():
        keys.append(f"rl:chat:ip:{ip.strip()}")
    return keys


def check_chat_rate_limit(
    user_id=None,
    ip: str | None = None,
    limit: int = CHAT_RATE_LIMIT,
    window_seconds: int = CHAT_RATE_WINDOW_SECONDS,
) -> tuple[bool, int]:
    try:
        now = time.time()
        keys = _get_rate_limit_keys(user_id=user_id, ip=ip)
        for key in keys:
            timestamps = cache.get(key) or []
            valid_timestamps = [t for t in timestamps if now - t < window_seconds]
            if len(valid_timestamps) >= limit:
                oldest = valid_timestamps[len(valid_timestamps) - limit]
                remaining = max(1, math.ceil(window_seconds - (now - oldest)))
                return False, remaining
        return True, 0
    except Exception as exc:
        logger.warning("Cache failure while checking chat rate limit: %s", exc)
        return True, 0


def record_chat_message(
    user_id=None,
    ip: str | None = None,
    window_seconds: int = CHAT_RATE_WINDOW_SECONDS,
) -> None:
    try:
        now = time.time()
        keys = _get_rate_limit_keys(user_id=user_id, ip=ip)
        for key in keys:
            timestamps = cache.get(key) or []
            valid_timestamps = [t for t in timestamps if now - t < window_seconds]
            valid_timestamps.append(now)
            cache.set(key, valid_timestamps, timeout=window_seconds)
    except Exception as exc:
        logger.warning("Cache failure while recording chat message: %s", exc)
