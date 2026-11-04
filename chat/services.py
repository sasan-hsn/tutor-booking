import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.db.models.functions import Greatest
from django.urls import reverse
from django.utils import timezone

from .models import Conversation, Message
from .utils import linkify_message_text

logger = logging.getLogger(__name__)


def broadcast_chat_message(message: Message) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    group_name = Conversation.get_group_name(message.conversation_id)
    payload = {
        "type": "chat_message",
        "message": {
            "id": message.id,
            "conversation_id": str(message.conversation_id),
            "sender_id": message.sender_id,
            "sender_username": message.sender.username,
            "text": message.text,
            "rendered_text": linkify_message_text(message.text),
            "message_type": message.message_type,
            "metadata": message.metadata,
            "created_at": message.created_at.isoformat(),
        },
    }
    try:
        async_to_sync(channel_layer.group_send)(group_name, payload)
    except Exception as exc:
        logger.warning(
            "Failed to broadcast chat message %s to group %s: %s",
            message.id,
            group_name,
            exc,
        )


def get_total_unread_chat_count(user) -> int:
    """
    Returns the total number of unread incoming chat messages across all
    active conversations for the given user.
    """
    if not user:
        return 0
    if hasattr(user, "is_authenticated") and not user.is_authenticated:
        return 0
    user_id = getattr(user, "id", user)
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return 0

    return (
        Message.objects.filter(
            (
                Q(conversation__student_id=user_id)
                & Q(id__gt=F("conversation__student_last_read_message_id"))
            )
            | (
                Q(conversation__teacher_id=user_id)
                & Q(id__gt=F("conversation__teacher_last_read_message_id"))
            )
        )
        .exclude(sender_id=user_id)
        .count()
    )


def broadcast_unread_chat_count(user) -> None:
    """
    Broadcasts the current total unread chat count to the user's private notification
    group (user_<user_id>) so that topbar navigation badges across all open tabs
    update immediately.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    user_id = getattr(user, "id", user)
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return

    unread_count = get_total_unread_chat_count(user_id)
    group_name = f"user_{user_id}"
    payload = {
        "type": "unread_chat_count",
        "count": unread_count,
    }
    try:
        async_to_sync(channel_layer.group_send)(group_name, payload)
    except Exception as exc:
        logger.warning(
            "Failed to broadcast unread_chat_count to group %s: %s",
            group_name,
            exc,
        )


def get_or_create_conversation(student, teacher) -> tuple[Conversation, bool]:
    if getattr(student, "role", None) != "student":
        raise ValidationError({"student": "Student must have the student role."})
    if getattr(teacher, "role", None) != "teacher":
        raise ValidationError({"teacher": "Teacher must have the teacher role."})
    if student.id == teacher.id:
        raise ValidationError({"teacher": "Self-conversations are not permitted."})

    with transaction.atomic():
        conversation, created = Conversation.objects.get_or_create(
            student=student,
            teacher=teacher,
        )
    return conversation, created


def advance_watermark(conversation: Conversation, user, message_id: int) -> int:
    if not message_id or message_id <= 0:
        return 0

    if user.id == conversation.student_id:
        field = "student_last_read_message_id"
    elif user.id == conversation.teacher_id:
        field = "teacher_last_read_message_id"
    else:
        raise ValueError("User is not a participant of this conversation.")

    old_val = Conversation.objects.filter(id=conversation.id).values_list(field, flat=True).first() or 0
    if message_id > old_val:
        Conversation.objects.filter(id=conversation.id).update(
            **{field: Greatest(F(field), message_id)}
        )
        updated_val = Conversation.objects.filter(id=conversation.id).values_list(
            field, flat=True
        ).first() or 0
        if updated_val > old_val:
            transaction.on_commit(lambda: broadcast_unread_chat_count(user))
        return updated_val

    return old_val


def send_message(
    conversation: Conversation,
    sender,
    text: str,
    message_type: str = Message.MessageType.TEXT,
    metadata: dict | None = None,
    snippet: str | None = None,
) -> Message:
    if metadata is None:
        metadata = {}

    stripped_text = text.strip() if text else ""
    if message_type == Message.MessageType.TEXT and not stripped_text:
        raise ValidationError({"text": "Message text cannot be empty or whitespace only."})

    if snippet is not None:
        resolved_snippet = snippet[:120]
    elif len(stripped_text) > 120:
        resolved_snippet = stripped_text[:117] + "..."
    else:
        resolved_snippet = stripped_text

    with transaction.atomic():
        message = Message(
            conversation=conversation,
            sender=sender,
            message_type=message_type,
            text=stripped_text,
            metadata=metadata,
        )
        message.full_clean()
        message.save()

        now = message.created_at or timezone.now()

        if sender.id == conversation.student_id:
            Conversation.objects.filter(id=conversation.id).update(
                last_message_at=now,
                last_message_snippet=resolved_snippet,
                student_last_read_message_id=Greatest(
                    F("student_last_read_message_id"),
                    message.id,
                ),
            )
        elif sender.id == conversation.teacher_id:
            Conversation.objects.filter(id=conversation.id).update(
                last_message_at=now,
                last_message_snippet=resolved_snippet,
                teacher_last_read_message_id=Greatest(
                    F("teacher_last_read_message_id"),
                    message.id,
                ),
            )

        recipient = conversation.get_counterpart(sender)
        transaction.on_commit(lambda: broadcast_chat_message(message))
        transaction.on_commit(lambda: broadcast_unread_chat_count(recipient))

    return message


def record_booking_event(booking, event_type: str) -> Message | None:
    """
    Records a structured system event in the conversation between the booking's
    student and teacher.

    Supported event types:
      - 'booking_requested': student submitted a lesson booking request
      - 'booking_confirmed': teacher accepted/confirmed the lesson
      - 'cancellation_requested': student requested lesson cancellation
      - 'booking_cancelled': lesson was cancelled (by teacher or cancellation accepted)

    Returns the created Message instance, or None if recording failed.
    """
    valid_events = {
        "booking_requested",
        "booking_confirmed",
        "cancellation_requested",
        "booking_cancelled",
    }
    if event_type not in valid_events:
        logger.warning("Unknown booking event_type '%s' passed to record_booking_event.", event_type)
        return None

    if not booking or not getattr(booking, "id", None):
        logger.warning("Invalid booking instance passed to record_booking_event.")
        return None

    try:
        student = booking.student
        teacher_user = booking.teacher.user

        conversation, _ = get_or_create_conversation(student=student, teacher=teacher_user)

        if event_type in ("booking_requested", "cancellation_requested"):
            sender = student
            action_url = reverse("booking:teacher_dashboard")
        else:
            sender = teacher_user
            action_url = ""

        formatted_date = booking.start_at.strftime("%b %d")

        if event_type == "booking_requested":
            snippet = f"📅 Lesson Request submitted for {formatted_date}"
        elif event_type == "booking_confirmed":
            snippet = f"✅ Lesson Confirmed for {formatted_date}"
        elif event_type == "cancellation_requested":
            snippet = f"⚠️ Cancellation Requested for {formatted_date}"
        elif event_type == "booking_cancelled":
            snippet = f"❌ Lesson Cancelled for {formatted_date}"
        else:
            snippet = f"📅 Booking update for {formatted_date}"

        metadata = {
            "event_type": event_type,
            "booking_id": booking.id,
            "student_name": student.get_full_name() or student.username,
            "lesson_type": booking.lesson_type,
            "lesson_type_display": booking.get_lesson_type_display(),
            "duration_minutes": booking.duration_minutes,
            "start_at": booking.start_at.isoformat(),
            "end_at": booking.end_at.isoformat(),
            "status": booking.status,
            "status_display": booking.get_status_display(),
            "action_url": action_url,
        }

        return send_message(
            conversation=conversation,
            sender=sender,
            text="",
            message_type=Message.MessageType.SYSTEM_EVENT,
            metadata=metadata,
            snippet=snippet,
        )
    except Exception as exc:
        logger.exception(
            "Failed to record booking event '%s' for booking #%s: %s",
            event_type,
            getattr(booking, "id", None),
            exc,
        )
        return None

