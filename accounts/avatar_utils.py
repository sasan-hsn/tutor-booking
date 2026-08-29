AVATAR_COLORS = [
    "#4A6FA5",
    "#C97B4A",
    "#5B8C5A",
    "#A55B8C",
    "#C94A5B",
    "#4AA5A0",
    "#8C7B5B",
    "#5B6FA5",
]


def get_avatar_color(user_id):
    return AVATAR_COLORS[user_id % len(AVATAR_COLORS)]