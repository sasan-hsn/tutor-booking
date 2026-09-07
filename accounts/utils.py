from functools import lru_cache
from zoneinfo import available_timezones


@lru_cache(maxsize=1)
def get_timezone_choices():
    return sorted(available_timezones())