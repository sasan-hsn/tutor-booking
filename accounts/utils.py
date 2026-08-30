from zoneinfo import available_timezones


def get_timezone_choices():
    return sorted(available_timezones())