from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
@require_POST
def mark_notification_read(request, pk):
    """
    Mark an individual notification as read for the authenticated recipient.
    Returns the updated unread count.
    """
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.mark_as_read()

    unread_count = Notification.objects.unread_count_for(request.user)
    return JsonResponse({"success": True, "unread_count": unread_count})


@login_required
@require_POST
def mark_all_notifications_read(request):
    """
    Mark all unread notifications as read for the authenticated recipient.
    Returns unread count of 0.
    """
    Notification.objects.unread_for(request.user).update(
        is_read=True,
        read_at=timezone.now(),
    )

    return JsonResponse({"success": True, "unread_count": 0})
