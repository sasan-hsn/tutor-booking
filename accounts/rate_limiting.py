import math
import time
from django.core.cache import cache


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
    """
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


def record_resend_attempt(
    user=None,
    email: str | None = None,
    ip: str | None = None,
    cooldown_seconds: int = 60,
) -> None:
    """Record a verification resend request to enforce cooldown and hourly sliding ceiling."""
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
