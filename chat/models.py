import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_conversations",
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="teacher_conversations",
    )
    student_last_read_message_id = models.BigIntegerField(default=0)
    teacher_last_read_message_id = models.BigIntegerField(default=0)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_message_snippet = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "teacher"],
                name="unique_student_teacher_conversation",
            ),
            models.CheckConstraint(
                condition=~models.Q(student=models.F("teacher")),
                name="prevent_self_conversation",
            ),
        ]

    def clean(self):
        super().clean()
        if self.student_id and self.teacher_id:
            if self.student_id == self.teacher_id:
                raise ValidationError({"teacher": "Self-conversations are not permitted."})
            if hasattr(self.student, "role") and self.student.role != "student":
                raise ValidationError({"student": "Student must have the student role."})
            if hasattr(self.teacher, "role") and self.teacher.role != "teacher":
                raise ValidationError({"teacher": "Teacher must have the teacher role."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def get_counterpart(self, user):
        if user == self.student:
            return self.teacher
        if user == self.teacher:
            return self.student
        raise ValueError("User is not a participant of this conversation.")

    def get_last_read_message_id(self, user):
        if user == self.student:
            return self.student_last_read_message_id
        if user == self.teacher:
            return self.teacher_last_read_message_id
        raise ValueError("User is not a participant of this conversation.")

    def get_unread_count(self, user):
        if user not in (self.student, self.teacher):
            return 0
        watermark = self.get_last_read_message_id(user)
        return self.messages.filter(id__gt=watermark).exclude(sender=user).count()

    def __str__(self):
        return f"Conversation between {self.student.username} and {self.teacher.username}"


class Message(models.Model):
    class MessageType(models.TextChoices):
        TEXT = "text", "Text"
        SYSTEM_EVENT = "system_event", "System Event"

    id = models.BigAutoField(primary_key=True)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_messages",
    )
    message_type = models.CharField(
        max_length=20,
        choices=MessageType.choices,
        default=MessageType.TEXT,
    )
    text = models.TextField(max_length=2000, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["conversation", "id"], name="chat_msg_conv_id_idx"),
            models.Index(fields=["conversation", "created_at"], name="chat_msg_conv_created_idx"),
        ]
        ordering = ["id"]

    def clean(self):
        super().clean()
        if self.message_type == self.MessageType.TEXT:
            if not self.text or not self.text.strip():
                raise ValidationError({"text": "Message text cannot be empty or whitespace only."})
            if len(self.text) > 2000:
                raise ValidationError({"text": "Message text cannot exceed 2000 characters."})

        if self.conversation_id and self.sender_id:
            if self.sender_id not in (self.conversation.student_id, self.conversation.teacher_id):
                raise ValidationError({"sender": "Sender must be a participant of the conversation."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Message #{self.id} in {self.conversation_id} by {self.sender.username}"
