import json
import logging
from zoneinfo import ZoneInfo
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Conversation, Message
from .services import advance_watermark, get_or_create_conversation, send_message
from .utils import check_chat_rate_limit, get_client_ip, linkify_message_text, record_chat_message

User = get_user_model()
logger = logging.getLogger(__name__)


def serialize_message(message: Message) -> dict:
    """Helper to serialize a Message model instance into a standard JSON-compatible dict."""
    return {
        "id": message.id,
        "sender_id": message.sender_id,
        "sender_username": message.sender.username,
        "text": message.text,
        "rendered_text": linkify_message_text(message.text),
        "message_type": message.message_type,
        "created_at": message.created_at.isoformat(),
    }


@login_required
@require_GET
def inbox_view(request):
    """
    Dedicated Conversation Inbox displaying all active conversations for the current user,
    ordered by last_message_at DESC. Uninitiated conversations (last_message_at is null)
    are omitted.
    """
    conversations = (
        Conversation.objects.filter(
            Q(student=request.user) | Q(teacher=request.user),
            last_message_at__isnull=False,
        )
        .select_related("student", "teacher")
        .order_by("-last_message_at")
    )

    user_tz_name = getattr(request.user, "timezone", "UTC") or "UTC"
    try:
        user_tz = ZoneInfo(user_tz_name)
    except Exception:
        user_tz = ZoneInfo("UTC")

    inbox_items = []
    for conv in conversations:
        counterpart = conv.get_counterpart(request.user)
        unread_count = conv.get_unread_count(request.user)
        local_last_message_at = None
        if conv.last_message_at:
            local_last_message_at = timezone.localtime(conv.last_message_at, user_tz).replace(tzinfo=None)

        inbox_items.append(
            {
                "conversation": conv,
                "counterpart": counterpart,
                "unread_count": unread_count,
                "local_last_message_at": local_last_message_at,
            }
        )

    context = {
        "conversations": inbox_items,
    }
    return render(request, "chat/inbox.html", context)


@login_required
@require_GET
def start_conversation(request, teacher_username):
    """
    Entrypoint for students to start or resume a private conversation with a teacher.
    Validates email verification and teacher role, then idempotently creates or resolves
    the Conversation and redirects to /messages/<conversation_id>/.
    """
    if request.user.role != "student":
        return HttpResponseForbidden("Only students can initiate a conversation with a teacher.")

    if not request.user.is_email_verified:
        return render(
            request,
            "chat/unverified_prompt.html",
            {"teacher_username": teacher_username},
            status=403,
        )

    teacher = get_object_or_404(
        User,
        username__iexact=teacher_username,
        role="teacher",
        is_active=True,
    )

    try:
        conversation, _ = get_or_create_conversation(
            student=request.user,
            teacher=teacher,
        )
    except ValidationError as exc:
        logger.warning("Conversation creation validation failed: %s", exc)
        return HttpResponseForbidden("Cannot create conversation.")

    return redirect("chat:conversation_detail", conversation_id=conversation.id)


@login_required
@require_GET
def conversation_detail(request, conversation_id):
    """
    Displays the conversation timeline between student and teacher.
    Restricted to conversation participants.
    Advances the viewer's read watermark.
    """
    conversation = get_object_or_404(
        Conversation.objects.select_related("student", "teacher"),
        id=conversation_id,
    )

    if request.user not in (conversation.student, conversation.teacher):
        raise Http404("Conversation not found.")

    counterpart = conversation.get_counterpart(request.user)

    messages = list(
        conversation.messages.select_related("sender").order_by("-id")[:30]
    )
    messages.reverse()

    if messages:
        latest_message = messages[-1]
        advance_watermark(conversation, request.user, latest_message.id)

    is_student = request.user.role == "student"
    counterpart_watermark = conversation.get_last_read_message_id(counterpart)

    oldest_initial_id = messages[0].id if messages else 0
    has_more_history = (
        conversation.messages.filter(id__lt=oldest_initial_id).exists()
        if oldest_initial_id
        else False
    )

    context = {
        "conversation": conversation,
        "counterpart": counterpart,
        "counterpart_watermark": counterpart_watermark,
        "messages": messages,
        "is_student": is_student,
        "is_email_verified": request.user.is_email_verified,
        "has_more_history": has_more_history,
    }
    return render(request, "chat/conversation_detail.html", context)


@login_required
@require_POST
def send_message_view(request, conversation_id):
    conversation = get_object_or_404(
        Conversation.objects.select_related("student", "teacher"),
        id=conversation_id,
    )

    if request.user not in (conversation.student, conversation.teacher):
        raise Http404("Conversation not found.")

    if not request.user.is_email_verified:
        return JsonResponse(
            {
                "error": "email_unverified",
                "message": "Please verify your email address to send messages.",
            },
            status=403,
        )

    client_ip = get_client_ip(request)
    allowed, retry_after = check_chat_rate_limit(user_id=request.user.id, ip=client_ip)
    if not allowed:
        return JsonResponse(
            {
                "error": "rate_limited",
                "retry_after": retry_after,
                "message": f"Too many messages. Please wait {retry_after} seconds.",
            },
            status=429,
        )

    raw_text = None
    if request.content_type == "application/json":
        try:
            body = json.loads(request.body.decode("utf-8"))
            raw_text = body.get("text")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"error": "invalid_json", "message": "Invalid JSON body."}, status=400)
    else:
        raw_text = request.POST.get("text")

    if not raw_text or not raw_text.strip():
        return JsonResponse({"error": "empty_text", "message": "Message text cannot be empty."}, status=400)

    if len(raw_text.strip()) > 2000:
        return JsonResponse({"error": "text_too_long", "message": "Message text cannot exceed 2000 characters."}, status=400)

    try:
        message = send_message(
            conversation=conversation,
            sender=request.user,
            text=raw_text,
        )
        record_chat_message(user_id=request.user.id, ip=client_ip)
    except ValidationError as exc:
        return JsonResponse({"error": "validation_error", "message": str(exc)}, status=400)

    return JsonResponse(
        {
            "success": True,
            "message": serialize_message(message),
        }
    )


@login_required
@require_GET
def conversation_history(request, conversation_id):
    """
    Reverse pagination API returning older messages in JSON format.
    Accepts ?before=<message_id>.
    Returns up to 30 older messages ordered chronologically (oldest to newest within the chunk).
    """
    conversation = get_object_or_404(
        Conversation.objects.select_related("student", "teacher"),
        id=conversation_id,
    )

    if request.user not in (conversation.student, conversation.teacher):
        raise Http404("Conversation not found.")

    before_raw = request.GET.get("before")
    if not before_raw:
        return JsonResponse({"error": "missing_before", "message": "Query parameter 'before' is required."}, status=400)

    try:
        before_id = int(before_raw)
        if before_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse({"error": "invalid_before", "message": "Query parameter 'before' must be a positive integer."}, status=400)

    PAGE_SIZE = 30
    # Fetch PAGE_SIZE + 1 messages with id < before_id in descending order to determine has_more
    older_messages_qs = (
        conversation.messages.filter(id__lt=before_id)
        .select_related("sender")
        .order_by("-id")[: PAGE_SIZE + 1]
    )
    raw_list = list(older_messages_qs)
    has_more = len(raw_list) > PAGE_SIZE
    messages_chunk = raw_list[:PAGE_SIZE]
    messages_chunk.reverse()  # Chronological order for prepending

    serialized = [serialize_message(msg) for msg in messages_chunk]

    return JsonResponse(
        {
            "messages": serialized,
            "has_more": has_more,
        }
    )

