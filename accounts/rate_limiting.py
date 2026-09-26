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


LOGIN_COMPOUND_LIMIT = 5
LOGIN_COMPOUND_WINDOW_SECONDS = 300

LOGIN_IP_LIMIT = 20
LOGIN_IP_WINDOW_SECONDS = 900

LOCKOUT_TYPE_COMPOUND = 'compound'
LOCKOUT_TYPE_IP_CEILING = 'ip_ceiling'


def normalize_username(username: str | None) -> str:
    """Normalize username by stripping leading/trailing whitespace and lowercasing."""
    if not username:
        return ''
    return username.strip().lower()


def _get_login_cache_key(ip: str | None, username: str | None) -> str | None:
    norm_user = normalize_username(username)
    if not ip or not ip.strip() or not norm_user:
        return None
    return f"rl:login:user_ip:{ip.strip()}:{norm_user}"


def _get_login_ip_cache_key(ip: str | None) -> str | None:
    if not ip or not ip.strip():
        return None
    return f"rl:login:ip:{ip.strip()}"


def _get_valid_timestamps(key: str, window_seconds: int, now: float) -> list[float]:
    try:
        timestamps = cache.get(key) or []
        return [t for t in timestamps if now - t < window_seconds]
    except Exception as exc:
        logger.warning("Cache read failed for key %s: %s", key, exc)
        return []


def _check_window_limit(
    key: str,
    limit: int,
    window_seconds: int,
    now: float,
) -> tuple[bool, int]:
    valid_timestamps = _get_valid_timestamps(key, window_seconds, now)
    if len(valid_timestamps) >= limit:
        idx = len(valid_timestamps) - limit
        oldest = valid_timestamps[idx]
        remaining = max(1, math.ceil(window_seconds - (now - oldest)))
        return False, remaining
    return True, 0


def _append_timestamp(key: str, window_seconds: int, now: float) -> None:
    valid_timestamps = _get_valid_timestamps(key, window_seconds, now)
    valid_timestamps.append(now)
    cache.set(key, valid_timestamps, timeout=window_seconds)


def check_login_ip_rate_limit(
    ip: str | None,
    limit: int = LOGIN_IP_LIMIT,
    window_seconds: int = LOGIN_IP_WINDOW_SECONDS,
) -> tuple[bool, int]:
    """Check if a login attempt is permitted for the given client IP under the global IP ceiling."""
    return check_login_rate_limit(
        ip=ip,
        username=None,
        ip_limit=limit,
        ip_window_seconds=window_seconds,
    )


def check_login_rate_limit(
    ip: str | None,
    username: str | None,
    limit: int = LOGIN_COMPOUND_LIMIT,
    window_seconds: int = LOGIN_COMPOUND_WINDOW_SECONDS,
    ip_limit: int = LOGIN_IP_LIMIT,
    ip_window_seconds: int = LOGIN_IP_WINDOW_SECONDS,
) -> tuple[bool, int]:
    """
    Check if a login attempt is permitted for the given (IP, username) pair and global IP ceiling.
    Returns (is_allowed, retry_after_seconds).
    Passive check: Does not write to cache, increment strikes, or extend TTL.
    Fails open if the cache backend is unavailable.
    Emits structured security warning when rate limit is exceeded.
    """
    try:
        now = time.time()
        clean_ip = ip.strip() if ip else ''
        norm_user = normalize_username(username)

        # 1. Global IP ceiling check
        ip_blocked = False
        ip_retry_after = 0
        if clean_ip:
            ip_key = _get_login_ip_cache_key(clean_ip)
            if ip_key:
                allowed, ip_retry_after = _check_window_limit(
                    ip_key, ip_limit, ip_window_seconds, now
                )
                if not allowed:
                    ip_blocked = True

        # 2. Compound (IP, username) check
        compound_blocked = False
        compound_retry_after = 0
        if clean_ip and norm_user:
            compound_key = _get_login_cache_key(clean_ip, norm_user)
            if compound_key:
                allowed, compound_retry_after = _check_window_limit(
                    compound_key, limit, window_seconds, now
                )
                if not allowed:
                    compound_blocked = True

        if ip_blocked or compound_blocked:
            if ip_blocked and compound_blocked:
                lockout_type = LOCKOUT_TYPE_IP_CEILING
                retry_after = max(ip_retry_after, compound_retry_after)
            elif ip_blocked:
                lockout_type = LOCKOUT_TYPE_IP_CEILING
                retry_after = ip_retry_after
            else:
                lockout_type = LOCKOUT_TYPE_COMPOUND
                retry_after = compound_retry_after

            logger.warning(
                "Login rate limit exceeded: ip=%s, username=%s, lockout_type=%s, retry_after=%s",
                clean_ip,
                norm_user,
                lockout_type,
                retry_after,
                extra={
                    'client_ip': clean_ip,
                    'normalized_username': norm_user,
                    'lockout_type': lockout_type,
                    'lockout_reason': lockout_type,
                    'retry_after': retry_after,
                    'retry_duration': retry_after,
                },
            )
            return False, retry_after

        return True, 0
    except Exception as exc:
        logger.warning("Cache failure while checking login rate limit: %s", exc)
        return True, 0


def record_login_failure(
    ip: str | None,
    username: str | None,
    window_seconds: int = LOGIN_COMPOUND_WINDOW_SECONDS,
    ip_window_seconds: int = LOGIN_IP_WINDOW_SECONDS,
) -> None:
    """Record a failed login attempt for the (IP, username) pair and global IP."""
    try:
        now = time.time()
        clean_ip = ip.strip() if ip else ''
        norm_user = normalize_username(username)

        # 1. Record failure in global IP bucket
        if clean_ip:
            ip_key = _get_login_ip_cache_key(clean_ip)
            if ip_key:
                _append_timestamp(ip_key, ip_window_seconds, now)

        # 2. Record failure in compound (IP, username) bucket
        if clean_ip and norm_user:
            compound_key = _get_login_cache_key(clean_ip, norm_user)
            if compound_key:
                _append_timestamp(compound_key, window_seconds, now)
    except Exception as exc:
        logger.warning(
            "Cache failure while recording login failure for ip=%s, username=%s: %s",
            ip,
            username,
            exc,
        )


def reset_login_rate_limit(ip: str | None, username: str | None) -> None:
    """Reset the failed attempts counter for the (IP, username) pair upon successful login."""
    try:
        key = _get_login_cache_key(ip, username)
        if not key:
            return

        cache.delete(key)
    except Exception as exc:
        logger.warning("Cache failure while resetting login rate limit for key %s: %s", key, exc)


def reset_login_ip_rate_limit(ip: str | None) -> None:
    """Reset the failed attempts counter for the global client IP."""
    try:
        clean_ip = ip.strip() if ip else ''
        if not clean_ip:
            return
        key = _get_login_ip_cache_key(clean_ip)
        if key:
            cache.delete(key)
    except Exception as exc:
        logger.warning("Cache failure while resetting IP login rate limit for ip %s: %s", ip, exc)




