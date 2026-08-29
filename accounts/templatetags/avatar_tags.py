from django import template
from accounts.avatar_utils import get_avatar_color

register = template.Library()


@register.inclusion_tag("partials/_avatar.html")
def user_avatar(user, size=40):
    profile_picture = None
    profile = getattr(user, "teacher_profile", None) or getattr(user, "student_profile", None)
    if profile and getattr(profile, "profile_picture", None):
        profile_picture = profile.profile_picture.url

    display_name = user.get_full_name() or user.username
    initial = display_name[0].upper() if display_name else "?"

    color = get_avatar_color(user.id)

    return {
        "profile_picture": profile_picture,
        "initial": initial,
        "color": color,
        "size": size,
    }