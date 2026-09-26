import logging
import math
import time
from django.core.cache import cache

logger = logging.getLogger(__name__)


def get_client_ip(request) -> str:
    """Extract client IP address, respecting reverse proxy headers."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _get_identifiers(user=None, email: str | None = None, ip: str | None = None) -> list[str]:
    identifiers = []
    if user and getattr(user, 'pk', None):
        identifiers.append(f"user:{user.pk}")
    if email and email.strip():
        identifiers.append(f"email:{email.strip().lower()}")
    if ip and ip.strip():
        identifiers.append(f"ip:{ip.strip()}")
    return identifiers


def check_resend_rate_limit(
    user=None,
    email: str | None = None,
    ip: str | None = None,
    cooldown_seconds: int = 60,
    hourly_limit: int = 5,
) -> tuple[bool, str | None, int]:
    """
    Check if a verification resend request is permitted.
    Returns (is_allowed, reason, retry_after_seconds).
    Reasons:
    - 'cooldown': request attempted before cooldown_seconds elapsed.
    - 'ceiling': max requests exceeded in sliding 1-hour window.
    - None: request allowed.
    Fails open if the cache backend is unavailable.
    """
    try:
        now = time.time()
        identifiers = _get_identifiers(user=user, email=email, ip=ip)

        for ident in identifiers:
            # 1. Cooldown check
            if cooldown_seconds > 0:
                cd_key = f"rl:verify_cd:{ident}"
                cd_expiry = cache.get(cd_key)
                if cd_expiry and cd_expiry > now:
                    remaining = max(1, math.ceil(cd_expiry - now))
                    return False, 'cooldown', remaining

            # 2. Hourly ceiling check
            hr_key = f"rl:verify_hr:{ident}"
            timestamps = cache.get(hr_key) or []
            valid_timestamps = [t for t in timestamps if now - t < 3600]

            if len(valid_timestamps) >= hourly_limit:
                oldest = valid_timestamps[0]
                remaining = max(1, math.ceil(3600 - (now - oldest)))
                return False, 'ceiling', remaining

        return True, None, 0
    except Exception as exc:
        logger.warning("Cache failure while checking resend rate limit: %s", exc)
        return True, None, 0


def record_resend_attempt(
    user=None,
    email: str | None = None,
    ip: str | None = None,
    cooldown_seconds: int = 60,
) -> None:
    """Record a verification resend request to enforce cooldown and hourly sliding ceiling."""
    try:
        now = time.time()
        identifiers = _get_identifiers(user=user, email=email, ip=ip)

        for ident in identifiers:
            if cooldown_seconds > 0:
                cd_key = f"rl:verify_cd:{ident}"
                cache.set(cd_key, now + cooldown_seconds, timeout=cooldown_seconds)

            hr_key = f"rl:verify_hr:{ident}"
            timestamps = cache.get(hr_key) or []
            valid_timestamps = [t for t in timestamps if now - t < 3600]
            valid_timestamps.append(now)
            cache.set(hr_key, valid_timestamps, timeout=3600)
    except Exception as exc:
        logger.warning("Cache failure while recording resend attempt: %s", exc)


def normalize_username(username: str | None) -> str:
    """Normalize username by stripping leading/trailing whitespace and lowercasing."""
    if not username:
        return ''
    return username.strip().lower()


def _get_login_cache_key(ip: str | None, username: str | None) -> str | None:
    norm_user = normalize_username(username)
    if not ip or not norm_user:
        return None
    return f"rl:login:user_ip:{ip.strip()}:{norm_user}"


def _get_valid_timestamps(key: str, window_seconds: int, now: float) -> list[float]:
    try:
        timestamps = cache.get(key) or []
        return [t for t in timestamps if now - t < window_seconds]
    except Exception as exc:
        logger.warning("Cache read failed for key %s: %s", key, exc)
        return []


def check_login_rate_limit(
    ip: str | None,
    username: str | None,
    limit: int = 5,
    window_seconds: int = 300,
) -> tuple[bool, int]:
    """
    Check if a login attempt is permitted for the given (IP, username) pair.
    Returns (is_allowed, retry_after_seconds).
    Passive check: Does not write to cache, increment strikes, or extend TTL.
    Fails open if the cache backend is unavailable.
    """
    try:
        key = _get_login_cache_key(ip, username)
        if not key:
            return True, 0

        now = time.time()
        valid_timestamps = _get_valid_timestamps(key, window_seconds, now)

        if len(valid_timestamps) >= limit:
            index_to_expire = len(valid_timestamps) - limit
            oldest = valid_timestamps[index_to_expire]
            remaining = max(1, math.ceil(window_seconds - (now - oldest)))
            return False, remaining

        return True, 0
    except Exception as exc:
        logger.warning("Cache failure while checking login rate limit: %s", exc)
        return True, 0


def record_login_failure(
    ip: str | None,
    username: str | None,
    window_seconds: int = 300,
) -> None:
    """Record a failed login attempt for the (IP, username) pair."""
    try:
        key = _get_login_cache_key(ip, username)
        if not key:
            return

        now = time.time()
        valid_timestamps = _get_valid_timestamps(key, window_seconds, now)
        valid_timestamps.append(now)
        cache.set(key, valid_timestamps, timeout=window_seconds)
    except Exception as exc:
        logger.warning("Cache failure while recording login failure for key %s: %s", key, exc)


def reset_login_rate_limit(ip: str | None, username: str | None) -> None:
    """Reset the failed attempts counter for the (IP, username) pair upon successful login."""
    try:
        key = _get_login_cache_key(ip, username)
        if not key:
            return

        cache.delete(key)
    except Exception as exc:
        logger.warning("Cache failure while resetting login rate limit for key %s: %s", key, exc)



