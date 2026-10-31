import json
import logging
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import Conversation, Message
from .services import advance_watermark, get_or_create_conversation, send_message
from .utils import check_chat_rate_limit, get_client_ip, linkify_message_text, record_chat_message

User = get_user_model()
logger = logging.getLogger(__name__)


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
        conversation.messages.select_related("sender").order_by("-id")[:50]
    )
    messages.reverse()

    if messages:
        latest_message = messages[-1]
        advance_watermark(conversation, request.user, latest_message.id)

    is_student = request.user.role == "student"

    context = {
        "conversation": conversation,
        "counterpart": counterpart,
        "messages": messages,
        "is_student": is_student,
        "is_email_verified": request.user.is_email_verified,
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
            "message": {
                "id": message.id,
                "sender_id": message.sender_id,
                "sender_username": message.sender.username,
                "text": message.text,
                "rendered_text": linkify_message_text(message.text),
                "message_type": message.message_type,
                "created_at": message.created_at.isoformat(),
            },
        }
    )
