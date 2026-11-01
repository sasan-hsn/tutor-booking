from datetime import datetime
from zoneinfo import ZoneInfo
from django import template
from django.utils import timezone
from chat.utils import linkify_message_text

register = template.Library()

register.filter("chat_linkify", linkify_message_text)


@register.filter
def parse_iso(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None
    return value


@register.filter
def local_date_time(value, user=None):
    """
    Parses an ISO datetime string and converts it to the given user's timezone,
    stripping tzinfo so Django's |date and |time filters don't re-convert it.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except Exception:
            return None
    else:
        return None

    if user and hasattr(user, "timezone") and user.timezone:
        try:
            tz = ZoneInfo(user.timezone)
            return timezone.localtime(dt, tz).replace(tzinfo=None)
        except Exception:
            pass
    return dt

