from django import template

register = template.Library()

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


@register.inclusion_tag("partials/_avatar.html")
def user_avatar(user, size=40):
    """
    Renders an avatar for a user: their uploaded profile picture if present,
    otherwise an initials-based fallback with a consistent color.
    `user` is expected to expose `.get_full_name()`/`.username` and,
    when available, a related profile with `.profile_picture`.
    """
    profile_picture = None
    profile = getattr(user, "teacher_profile", None) or getattr(user, "student_profile", None)
    if profile and getattr(profile, "profile_picture", None):
        profile_picture = profile.profile_picture.url

    display_name = user.get_full_name() or user.username
    initial = display_name[0].upper() if display_name else "?"

    color = AVATAR_COLORS[user.id % len(AVATAR_COLORS)]

    return {
        "profile_picture": profile_picture,
        "initial": initial,
        "color": color,
        "size": size,
    }