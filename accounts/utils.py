from functools import lru_cache
from zoneinfo import available_timezones

from django.utils.http import url_has_allowed_host_and_scheme


@lru_cache(maxsize=1)
def get_timezone_choices():
    return sorted(available_timezones())


def get_safe_redirect_url(request, url: str | None) -> str | None:
    """
    Validate a redirect URL to ensure it is safe to pass to redirect().

    Returns the sanitized URL if valid, or None if invalid or unsafe.

    Security rules:
    - Must be a non-empty string.
    - Must be an absolute path (starts with single '/') or an absolute URL (starts with http:// or https://).
    - Bare relative strings (e.g. 'fvkkuvfk', 'relative/path') are rejected to prevent
      NoReverseMatch crashes in Django's resolve_url() and directory traversal/unexpected relative jumps.
    - Protocol-relative URLs (starts with '//') are rejected to prevent open redirects.
    - Must satisfy url_has_allowed_host_and_scheme against the current request host and scheme.
    """
    if not url or not isinstance(url, str):
        return None

    cleaned_url = url.strip()
    if not cleaned_url:
        return None

    # Reject protocol-relative URLs (e.g. '//evil.com')
    if cleaned_url.startswith("//"):
        return None

    # Must start with a single '/' (site-relative path) or an absolute http(s) URL
    if not (
        cleaned_url.startswith("/") or cleaned_url.startswith(("http://", "https://"))
    ):
        return None

    if not url_has_allowed_host_and_scheme(
        cleaned_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return None

    return cleaned_url
