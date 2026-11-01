from .services import get_total_unread_chat_count


def chat_context(request):
    """
    Exposes total unread chat messages count to template context
    for authenticated users across all pages.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {
            "unread_chat_count": 0,
        }

    return {
        "unread_chat_count": get_total_unread_chat_count(user),
    }
