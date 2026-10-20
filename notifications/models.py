from django.conf import settings
from django.db import models
from django.utils import timezone


class NotificationQuerySet(models.QuerySet):
    def unread_for(self, user):
        return self.filter(recipient=user, is_read=False)

    def unread_count_for(self, user):
        user_id = user.id if hasattr(user, 'id') else user
        return self.filter(recipient_id=user_id, is_read=False).count()


class Notification(models.Model):
    class EventType(models.TextChoices):
        BOOKING_REQUESTED = 'booking_requested', 'Booking Requested'
        BOOKING_CONFIRMED = 'booking_confirmed', 'Booking Confirmed'
        BOOKING_DECLINED = 'booking_declined', 'Booking Declined'
        CANCELLATION_REQUESTED = 'cancellation_requested', 'Cancellation Requested'
        BOOKING_CANCELLED = 'booking_cancelled', 'Booking Cancelled'
        BOOKING_EXPIRED = 'booking_expired', 'Booking Expired'

    class Level(models.TextChoices):
        INFO = 'info', 'Info'
        SUCCESS = 'success', 'Success'
        WARNING = 'warning', 'Warning'

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='notifications_triggered',
    )
    event_type = models.CharField(
        max_length=50,
        choices=EventType.choices,
        db_index=True,
    )
    level = models.CharField(
        max_length=20,
        choices=Level.choices,
        default=Level.INFO,
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    action_url = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['recipient', 'is_read', '-created_at'],
                name='notif_recip_read_created_idx',
            ),
        ]

    @classmethod
    def get_group_name(cls, user_id):
        return f"user_{user_id}"

    def mark_as_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])

    def to_ws_payload(self, unread_count):
        return {
            "type": "notification_message",
            "id": self.id,
            "event_type": self.event_type,
            "level": self.level,
            "title": self.title,
            "message": self.message,
            "action_url": self.action_url,
            "created_at": self.created_at.isoformat(),
            "unread_count": unread_count,
        }

    def __str__(self):
        return f"Notification({self.recipient_id}, {self.event_type}, is_read={self.is_read})"
