import logging
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from .models import Conversation, Message

logger = logging.getLogger(__name__)


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
        Conversation.objects.filter(id=conversation.id).update(
            student_last_read_message_id=Greatest(
                F("student_last_read_message_id"),
                message_id,
            )
        )
    elif user.id == conversation.teacher_id:
        Conversation.objects.filter(id=conversation.id).update(
            teacher_last_read_message_id=Greatest(
                F("teacher_last_read_message_id"),
                message_id,
            )
        )
    else:
        raise ValueError("User is not a participant of this conversation.")

    return message_id


def send_message(
    conversation: Conversation,
    sender,
    text: str,
    message_type: str = Message.MessageType.TEXT,
    metadata: dict | None = None,
) -> Message:
    if metadata is None:
        metadata = {}

    stripped_text = text.strip() if text else ""
    if message_type == Message.MessageType.TEXT and not stripped_text:
        raise ValidationError({"text": "Message text cannot be empty or whitespace only."})

    if len(stripped_text) > 120:
        snippet = stripped_text[:117] + "..."
    else:
        snippet = stripped_text

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
                last_message_snippet=snippet,
                student_last_read_message_id=Greatest(
                    F("student_last_read_message_id"),
                    message.id,
                ),
            )
        elif sender.id == conversation.teacher_id:
            Conversation.objects.filter(id=conversation.id).update(
                last_message_at=now,
                last_message_snippet=snippet,
                teacher_last_read_message_id=Greatest(
                    F("teacher_last_read_message_id"),
                    message.id,
                ),
            )

    return message
