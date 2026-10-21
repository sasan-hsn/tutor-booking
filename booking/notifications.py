import logging
from django.urls import reverse

from notifications.models import Notification
from notifications.services import create_and_send_notification

logger = logging.getLogger(__name__)


def _build_booking_metadata(booking, participant_name: str) -> dict:
    """Build standardized contextual metadata dictionary for booking notifications."""
    return {
        "booking_id": booking.id,
        "lesson_duration": booking.duration_minutes,
        "start_time": booking.start_at.isoformat(),
        "participant_name": participant_name,
        "lesson_type": booking.lesson_type,
    }


def notify_booking_requested(booking):
    """Notify the teacher when a student submits a new booking request."""
    try:
        teacher_user = booking.teacher.user
        action_url = reverse("booking:teacher_dashboard")
        metadata = _build_booking_metadata(booking, booking.student_display_name)
        lesson_type = booking.get_lesson_type_display()
        return create_and_send_notification(
            recipient=teacher_user,
            actor=booking.student,
            event_type=Notification.EventType.BOOKING_REQUESTED,
            level=Notification.Level.INFO,
            title="New Lesson Request",
            message=f"{booking.student_display_name} requested a {booking.duration_minutes}-minute {lesson_type}.",
            action_url=action_url,
            metadata=metadata,
        )
    except Exception:
        logger.exception("Failed to emit booking_requested notification for booking #%s", booking.id)
        return None


def notify_booking_confirmed(booking):
    """Notify the student when the teacher confirms their booking request."""
    try:
        action_url = reverse("booking:student_dashboard")
        metadata = _build_booking_metadata(booking, booking.teacher_display_name)
        lesson_type = booking.get_lesson_type_display()
        return create_and_send_notification(
            recipient=booking.student,
            actor=booking.teacher.user,
            event_type=Notification.EventType.BOOKING_CONFIRMED,
            level=Notification.Level.SUCCESS,
            title="Lesson Confirmed",
            message=f"Your {lesson_type} with {booking.teacher_display_name} has been confirmed.",
            action_url=action_url,
            metadata=metadata,
        )
    except Exception:
        logger.exception("Failed to emit booking_confirmed notification for booking #%s", booking.id)
        return None


def notify_booking_declined(booking):
    """Notify the student when the teacher declines their booking request."""
    try:
        action_url = reverse("booking:student_dashboard")
        metadata = _build_booking_metadata(booking, booking.teacher_display_name)
        lesson_type = booking.get_lesson_type_display()
        return create_and_send_notification(
            recipient=booking.student,
            actor=booking.teacher.user,
            event_type=Notification.EventType.BOOKING_DECLINED,
            level=Notification.Level.WARNING,
            title="Lesson Request Declined",
            message=f"{booking.teacher_display_name} was unable to accept your {lesson_type} request.",
            action_url=action_url,
            metadata=metadata,
        )
    except Exception:
        logger.exception("Failed to emit booking_declined notification for booking #%s", booking.id)
        return None


def notify_cancellation_requested(booking):
    """Notify the teacher when a student requests cancellation of a lesson."""
    try:
        teacher_user = booking.teacher.user
        action_url = reverse("booking:teacher_dashboard")
        metadata = _build_booking_metadata(booking, booking.student_display_name)
        lesson_type = booking.get_lesson_type_display()
        return create_and_send_notification(
            recipient=teacher_user,
            actor=booking.student,
            event_type=Notification.EventType.CANCELLATION_REQUESTED,
            level=Notification.Level.WARNING,
            title="Cancellation Requested",
            message=f"{booking.student_display_name} requested to cancel their upcoming {lesson_type}.",
            action_url=action_url,
            metadata=metadata,
        )
    except Exception:
        logger.exception("Failed to emit cancellation_requested notification for booking #%s", booking.id)
        return None


def notify_booking_cancelled(booking, cancelled_by=None):
    """Notify the student when their booking is cancelled by the teacher."""
    try:
        action_url = reverse("booking:student_dashboard")
        metadata = _build_booking_metadata(booking, booking.teacher_display_name)
        actor = cancelled_by if cancelled_by is not None else booking.teacher.user
        lesson_type = booking.get_lesson_type_display()
        return create_and_send_notification(
            recipient=booking.student,
            actor=actor,
            event_type=Notification.EventType.BOOKING_CANCELLED,
            level=Notification.Level.WARNING,
            title="Lesson Cancelled",
            message=f"Your {lesson_type} with {booking.teacher_display_name} has been cancelled.",
            action_url=action_url,
            metadata=metadata,
        )
    except Exception:
        logger.exception("Failed to emit booking_cancelled notification for booking #%s", booking.id)
        return None


def notify_booking_expired(booking):
    """Notify both student and teacher when a pending booking expires."""
    notifications = []
    lesson_type = booking.get_lesson_type_display()
    # 1. Notify student
    try:
        action_url_student = reverse("booking:student_dashboard")
        student_metadata = _build_booking_metadata(booking, booking.teacher_display_name)
        notif_student = create_and_send_notification(
            recipient=booking.student,
            actor=None,
            event_type=Notification.EventType.BOOKING_EXPIRED,
            level=Notification.Level.WARNING,
            title="Lesson Request Expired",
            message=f"Your {lesson_type} request with {booking.teacher_display_name} has expired.",
            action_url=action_url_student,
            metadata=student_metadata,
        )
        notifications.append(notif_student)
    except Exception:
        logger.exception("Failed to emit booking_expired notification to student for booking #%s", booking.id)

    # 2. Notify teacher
    try:
        teacher_user = booking.teacher.user
        action_url_teacher = reverse("booking:teacher_dashboard")
        teacher_metadata = _build_booking_metadata(booking, booking.student_display_name)
        notif_teacher = create_and_send_notification(
            recipient=teacher_user,
            actor=None,
            event_type=Notification.EventType.BOOKING_EXPIRED,
            level=Notification.Level.WARNING,
            title="Lesson Request Expired",
            message=f"The {lesson_type} request from {booking.student_display_name} has expired.",
            action_url=action_url_teacher,
            metadata=teacher_metadata,
        )
        notifications.append(notif_teacher)
    except Exception:
        logger.exception("Failed to emit booking_expired notification to teacher for booking #%s", booking.id)

    return notifications
