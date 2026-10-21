from .models import Notification


def notification_context(request):
    """
    Exposes recent notifications and unread badge count to template context
    for authenticated users across all dashboard pages.
    """
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {
            'recent_notifications': [],
            'unread_notifications_count': 0,
        }

    return {
        'recent_notifications': Notification.objects.filter(
            recipient=user
        ).order_by('-created_at')[:20],
        'unread_notifications_count': Notification.objects.unread_count_for(user),
    }
