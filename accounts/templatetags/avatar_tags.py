from django import template
from accounts.avatar_utils import get_avatar_color

register = template.Library()


@register.inclusion_tag("partials/_avatar.html")
def user_avatar(user, size=40):
    profile_picture = None
    profile = getattr(user, "teacher_profile", None) or getattr(user, "student_profile", None)

    if profile and profile.profile_picture:
        try:
            profile_picture = profile.profile_picture.url
        except ValueError:
            profile_picture = None

    display_name = user.get_full_name() or getattr(user, "username", "")
    initial = display_name[0].upper() if display_name else "?"

    user_id = getattr(user, "id", 0)
    color = get_avatar_color(user_id)

    return {
        "profile_picture": profile_picture,
        "initial": initial,
        "color": color,
        "size": size,
        "display_name": display_name,
    }