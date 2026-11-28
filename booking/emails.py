import logging
from datetime import timezone as dt_timezone
from typing import NamedTuple
from urllib.parse import urlparse

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from payments.emails import build_dedup_key, send_dedup_multipart_email

logger = logging.getLogger(__name__)


def send_booking_request_student_email(booking) -> bool:
    """Send confirmation email to the student that their lesson request was submitted."""
    if not booking.student.email:
        logger.info(
            "Student %s for booking #%s has no email address; skipping confirmation email.",
            booking.student_id,
            booking.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    student_dashboard_url = f"{site_url}{reverse('booking:student_dashboard')}"
    local_start = booking.student_local_start
    local_end = booking.student_local_end

    context = {
        "booking": booking,
        "student": booking.student,
        "student_name": booking.student_display_name,
        "teacher_name": booking.teacher_display_name,
        "header_title": booking.teacher_display_name,
        "header_subtitle": booking.teacher.headline,
        "footer_brand": booking.teacher_display_name,
        "lesson_type": booking.get_lesson_type_display(),
        "date": local_start.strftime("%A, %B %d, %Y"),
        "time": f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
        "timezone": booking.student.timezone,
        "student_dashboard_url": student_dashboard_url,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
    }

    subject = f"Lesson Request Received – {booking.teacher_display_name}"
    text_content = render_to_string(
        "booking/emails/lesson_requested_student.txt", context
    )
    html_content = render_to_string(
        "booking/emails/lesson_requested_student.html", context
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[booking.student.email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info(
        "Sent lesson request confirmation email to student %s for booking #%s.",
        booking.student.email,
        booking.id,
    )
    return True


def send_booking_request_teacher_email(booking) -> bool:
    """Send alert email to the teacher that a new lesson request has arrived."""
    teacher_email = booking.teacher.user.email or booking.teacher.contact_email
    if not teacher_email:
        logger.info(
            "Teacher %s for booking #%s has no email address; skipping request alert email.",
            booking.teacher_id,
            booking.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    dashboard_url = f"{site_url}{reverse('booking:teacher_dashboard')}"
    local_start = booking.teacher_local_start
    local_end = booking.teacher_local_end

    context = {
        "booking": booking,
        "teacher": booking.teacher,
        "teacher_name": booking.teacher_display_name,
        "student_name": booking.student_display_name,
        "student_email": booking.student.email,
        "header_title": site_name,
        "header_subtitle": "Teacher Dashboard",
        "footer_brand": site_name,
        "lesson_type": booking.get_lesson_type_display(),
        "date": local_start.strftime("%A, %B %d, %Y"),
        "time": f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
        "timezone": booking.teacher.user.timezone,
        "dashboard_url": dashboard_url,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
    }

    student_display = context["student_name"]
    subject = f"New Lesson Request from {student_display} – {site_name}"
    text_content = render_to_string(
        "booking/emails/lesson_requested_teacher.txt", context
    )
    html_content = render_to_string(
        "booking/emails/lesson_requested_teacher.html", context
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[teacher_email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info(
        "Sent new lesson request alert email to teacher %s for booking #%s.",
        teacher_email,
        booking.id,
    )
    return True


def _escape_ics_text(text: str) -> str:
    """Escape special characters according to RFC 5545 section 3.3.11."""
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", r"\;")
    text = text.replace(",", r"\,")
    text = text.replace("\r\n", r"\n").replace("\r", r"\n").replace("\n", r"\n")
    return text


def _fold_ics_line(line: str) -> str:
    """Fold an RFC 5545 line to max 75 octets per line (excluding CRLF)."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line

    parts = []
    current_bytes = bytearray()

    for char in line:
        char_bytes = char.encode("utf-8")
        limit = 74 if parts else 75
        if len(current_bytes) + len(char_bytes) > limit:
            parts.append(current_bytes.decode("utf-8"))
            current_bytes = bytearray(char_bytes)
        else:
            current_bytes.extend(char_bytes)

    if current_bytes:
        parts.append(current_bytes.decode("utf-8"))

    return "\r\n ".join(parts)


def build_booking_ics(booking, site_domain: str = None) -> str:
    """Generate an RFC 5545 compliant .ics string for a confirmed booking."""
    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    if not site_domain:
        parsed_netloc = urlparse(site_url).netloc
        site_domain = (
            parsed_netloc.split(":")[0] if parsed_netloc else "englishwithmary.ir"
        )

    now_utc = timezone.now().astimezone(dt_timezone.utc)
    start_utc = booking.start_at.astimezone(dt_timezone.utc)
    end_utc = booking.end_at.astimezone(dt_timezone.utc)

    teacher_name = booking.teacher_display_name
    lesson_type = booking.get_lesson_type_display()
    meeting_link = (booking.teacher.meeting_link or "").strip()

    summary = f"{lesson_type} with {teacher_name}"
    dashboard_url = f"{site_url}{reverse('booking:student_dashboard')}"

    if meeting_link:
        description = (
            f"Lesson: {lesson_type} with {teacher_name}\n"
            f"Meeting Link: {meeting_link}\n"
            f"Student Dashboard: {dashboard_url}"
        )
    else:
        description = (
            f"Lesson: {lesson_type} with {teacher_name}\n"
            f"Meeting Link: Your teacher will share the meeting link prior to the lesson.\n"
            f"Student Dashboard: {dashboard_url}"
        )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{site_domain}//Tutor Booking//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:booking-{booking.id}@{site_domain}",
        f"DTSTAMP:{now_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{_escape_ics_text(summary)}",
        f"DESCRIPTION:{_escape_ics_text(description)}",
    ]

    if meeting_link:
        lines.append(f"LOCATION:{_escape_ics_text(meeting_link)}")

    lines.extend(
        [
            "STATUS:CONFIRMED",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )

    folded_lines = [_fold_ics_line(line) for line in lines]
    return "\r\n".join(folded_lines) + "\r\n"


def send_booking_confirmed_student_email(booking) -> bool:
    """Send confirmation email with calendar invite to student when teacher confirms a lesson request."""
    if not booking.student.email:
        logger.info(
            "Student %s for booking #%s has no email address; skipping confirmation email.",
            booking.student_id,
            booking.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    student_dashboard_url = f"{site_url}{reverse('booking:student_dashboard')}"
    local_start = booking.student_local_start
    local_end = booking.student_local_end
    meeting_link = (booking.teacher.meeting_link or "").strip()

    context = {
        "booking": booking,
        "student": booking.student,
        "student_name": booking.student_display_name,
        "teacher_name": booking.teacher_display_name,
        "header_title": booking.teacher_display_name,
        "header_subtitle": booking.teacher.headline,
        "footer_brand": booking.teacher_display_name,
        "lesson_type": booking.get_lesson_type_display(),
        "date": local_start.strftime("%A, %B %d, %Y"),
        "time": f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
        "timezone": booking.student.timezone,
        "meeting_link": meeting_link,
        "student_dashboard_url": student_dashboard_url,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
    }

    subject = f"Lesson Confirmed – {booking.teacher_display_name}"
    text_content = render_to_string(
        "booking/emails/lesson_confirmed_student.txt", context
    )
    html_content = render_to_string(
        "booking/emails/lesson_confirmed_student.html", context
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[booking.student.email],
    )
    email.attach_alternative(html_content, "text/html")

    ics_content = build_booking_ics(booking, site_domain=site_domain)
    email.attach("invite.ics", ics_content, "text/calendar")

    email.send(fail_silently=False)
    logger.info(
        "Sent lesson confirmed email to student %s for booking #%s.",
        booking.student.email,
        booking.id,
    )
    return True


def send_booking_declined_student_email(booking) -> bool:
    """Send polite notification to student when teacher declines a pending lesson request."""
    if not booking.student.email:
        logger.info(
            "Student %s for booking #%s has no email address; skipping declined email.",
            booking.student_id,
            booking.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    student_dashboard_url = f"{site_url}{reverse('booking:student_dashboard')}"
    book_lesson_url = f"{site_url}{reverse('booking:student_booking')}"
    local_start = booking.student_local_start
    local_end = booking.student_local_end

    context = {
        "booking": booking,
        "student": booking.student,
        "student_name": booking.student_display_name,
        "teacher_name": booking.teacher_display_name,
        "header_title": booking.teacher_display_name,
        "header_subtitle": booking.teacher.headline,
        "footer_brand": booking.teacher_display_name,
        "lesson_type": booking.get_lesson_type_display(),
        "date": local_start.strftime("%A, %B %d, %Y"),
        "time": f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
        "timezone": booking.student.timezone,
        "student_dashboard_url": student_dashboard_url,
        "book_lesson_url": book_lesson_url,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
    }

    subject = f"Lesson Request Declined – {booking.teacher_display_name}"
    text_content = render_to_string(
        "booking/emails/lesson_declined_student.txt", context
    )
    html_content = render_to_string(
        "booking/emails/lesson_declined_student.html", context
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[booking.student.email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info(
        "Sent lesson declined email to student %s for booking #%s.",
        booking.student.email,
        booking.id,
    )
    return True


def send_booking_cancelled_student_email(booking) -> bool:
    """Send notification to student when teacher cancels a confirmed lesson."""
    if not booking.student.email:
        logger.info(
            "Student %s for booking #%s has no email address; skipping cancelled email.",
            booking.student_id,
            booking.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    student_dashboard_url = f"{site_url}{reverse('booking:student_dashboard')}"
    book_lesson_url = f"{site_url}{reverse('booking:student_booking')}"
    local_start = booking.student_local_start
    local_end = booking.student_local_end

    context = {
        "booking": booking,
        "student": booking.student,
        "student_name": booking.student_display_name,
        "teacher_name": booking.teacher_display_name,
        "header_title": booking.teacher_display_name,
        "header_subtitle": booking.teacher.headline,
        "footer_brand": booking.teacher_display_name,
        "lesson_type": booking.get_lesson_type_display(),
        "date": local_start.strftime("%A, %B %d, %Y"),
        "time": f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
        "timezone": booking.student.timezone,
        "student_dashboard_url": student_dashboard_url,
        "book_lesson_url": book_lesson_url,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
    }

    subject = f"Lesson Cancelled – {booking.teacher_display_name}"
    text_content = render_to_string(
        "booking/emails/lesson_cancelled_student.txt", context
    )
    html_content = render_to_string(
        "booking/emails/lesson_cancelled_student.html", context
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[booking.student.email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info(
        "Sent lesson cancelled email to student %s for booking #%s.",
        booking.student.email,
        booking.id,
    )
    return True


def send_cancellation_requested_teacher_email(booking) -> bool:
    """Send alert to teacher when student requests lesson cancellation."""
    teacher_email = booking.teacher.user.email or booking.teacher.contact_email
    if not teacher_email:
        logger.info(
            "Teacher %s for booking #%s has no email address; skipping cancellation request alert email.",
            booking.teacher_id,
            booking.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    dashboard_url = f"{site_url}{reverse('booking:teacher_dashboard')}"
    local_start = booking.teacher_local_start
    local_end = booking.teacher_local_end

    context = {
        "booking": booking,
        "teacher": booking.teacher,
        "teacher_name": booking.teacher_display_name,
        "student_name": booking.student_display_name,
        "student_email": booking.student.email,
        "header_title": site_name,
        "header_subtitle": "Teacher Dashboard",
        "footer_brand": site_name,
        "lesson_type": booking.get_lesson_type_display(),
        "date": local_start.strftime("%A, %B %d, %Y"),
        "time": f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
        "timezone": booking.teacher.user.timezone,
        "dashboard_url": dashboard_url,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
    }

    student_display = context["student_name"]
    subject = f"Cancellation Request from {student_display} – {site_name}"
    text_content = render_to_string(
        "booking/emails/cancellation_requested_teacher.txt", context
    )
    html_content = render_to_string(
        "booking/emails/cancellation_requested_teacher.html", context
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[teacher_email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info(
        "Sent cancellation requested alert email to teacher %s for booking #%s.",
        teacher_email,
        booking.id,
    )
    return True


def send_booking_reminder_24h_student_email(booking) -> bool:
    """Send 24-hour advance reminder email to student for upcoming confirmed lesson."""
    if not booking.student.email:
        logger.info(
            "Student %s for booking #%s has no email address; skipping 24h reminder email.",
            booking.student_id,
            booking.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    student_dashboard_url = f"{site_url}{reverse('booking:student_dashboard')}"
    local_start = booking.student_local_start
    local_end = booking.student_local_end
    meeting_link = (booking.teacher.meeting_link or "").strip()

    context = {
        "booking": booking,
        "student": booking.student,
        "student_name": booking.student_display_name,
        "teacher_name": booking.teacher_display_name,
        "header_title": booking.teacher_display_name,
        "header_subtitle": booking.teacher.headline,
        "footer_brand": booking.teacher_display_name,
        "lesson_type": booking.get_lesson_type_display(),
        "date": local_start.strftime("%A, %B %d, %Y"),
        "time": f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
        "timezone": booking.student.timezone,
        "meeting_link": meeting_link,
        "student_dashboard_url": student_dashboard_url,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
    }

    subject = (
        f"Lesson Reminder: Tomorrow with {booking.teacher_display_name} – {site_name}"
    )
    text_content = render_to_string(
        "booking/emails/lesson_reminder_24h_student.txt", context
    )
    html_content = render_to_string(
        "booking/emails/lesson_reminder_24h_student.html", context
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[booking.student.email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info(
        "Sent 24h reminder email to student %s for booking #%s.",
        booking.student.email,
        booking.id,
    )
    return True


def send_booking_reminder_1h_student_email(booking) -> bool:
    """Send 1-hour advance reminder email to student for upcoming confirmed lesson."""
    if not booking.student.email:
        logger.info(
            "Student %s for booking #%s has no email address; skipping 1h reminder email.",
            booking.student_id,
            booking.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    student_dashboard_url = f"{site_url}{reverse('booking:student_dashboard')}"
    local_start = booking.student_local_start
    local_end = booking.student_local_end
    meeting_link = (booking.teacher.meeting_link or "").strip()

    context = {
        "booking": booking,
        "student": booking.student,
        "student_name": booking.student_display_name,
        "teacher_name": booking.teacher_display_name,
        "header_title": booking.teacher_display_name,
        "header_subtitle": booking.teacher.headline,
        "footer_brand": booking.teacher_display_name,
        "lesson_type": booking.get_lesson_type_display(),
        "date": local_start.strftime("%A, %B %d, %Y"),
        "time": f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
        "timezone": booking.student.timezone,
        "meeting_link": meeting_link,
        "student_dashboard_url": student_dashboard_url,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
    }

    subject = f"Lesson Starting in 1 Hour – {booking.teacher_display_name}"
    text_content = render_to_string(
        "booking/emails/lesson_reminder_1h_student.txt", context
    )
    html_content = render_to_string(
        "booking/emails/lesson_reminder_1h_student.html", context
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[booking.student.email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info(
        "Sent 1h reminder email to student %s for booking #%s.",
        booking.student.email,
        booking.id,
    )
    return True


def send_booking_reminder_1h_teacher_email(booking) -> bool:
    """Send 1-hour advance reminder email to teacher for upcoming confirmed lesson."""
    teacher_email = booking.teacher.user.email or booking.teacher.contact_email
    if not teacher_email:
        logger.info(
            "Teacher %s for booking #%s has no email address; skipping 1h reminder email.",
            booking.teacher_id,
            booking.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    dashboard_url = f"{site_url}{reverse('booking:teacher_dashboard')}"
    local_start = booking.teacher_local_start
    local_end = booking.teacher_local_end
    meeting_link = (booking.teacher.meeting_link or "").strip()

    context = {
        "booking": booking,
        "teacher": booking.teacher,
        "teacher_name": booking.teacher_display_name,
        "student_name": booking.student_display_name,
        "student_email": booking.student.email,
        "header_title": site_name,
        "header_subtitle": "Teacher Dashboard",
        "footer_brand": site_name,
        "lesson_type": booking.get_lesson_type_display(),
        "date": local_start.strftime("%A, %B %d, %Y"),
        "time": f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
        "timezone": booking.teacher.user.timezone,
        "meeting_link": meeting_link,
        "dashboard_url": dashboard_url,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
    }

    student_display = context["student_name"]
    subject = f"Upcoming Lesson in 1 Hour: {student_display} – {site_name}"
    text_content = render_to_string(
        "booking/emails/lesson_reminder_1h_teacher.txt", context
    )
    html_content = render_to_string(
        "booking/emails/lesson_reminder_1h_teacher.html", context
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[teacher_email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info(
        "Sent 1h reminder email to teacher %s for booking #%s.",
        teacher_email,
        booking.id,
    )
    return True


def send_teacher_daily_digest_email(teacher, target_date, bookings) -> bool:
    """Send consolidated daily schedule digest email to the teacher for tomorrow's lessons."""
    teacher_email = teacher.user.email or teacher.contact_email
    if not teacher_email:
        logger.info(
            "Teacher %s has no email address; skipping daily digest email.",
            teacher.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    dashboard_url = f"{site_url}{reverse('booking:teacher_dashboard')}"
    meeting_link = (teacher.meeting_link or "").strip()

    lessons_data = []
    for booking in bookings:
        local_start = booking.teacher_local_start
        local_end = booking.teacher_local_end
        lessons_data.append(
            {
                "booking": booking,
                "student_name": booking.student_display_name,
                "student_email": booking.student.email,
                "lesson_type": booking.get_lesson_type_display(),
                "start_time": local_start.strftime("%H:%M"),
                "end_time": local_end.strftime("%H:%M"),
                "time": f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
                "student_note": getattr(booking, "student_note", None)
                or getattr(booking, "notes", None)
                or "",
            }
        )

    total_lessons = len(lessons_data)
    date_str = target_date.strftime("%A, %B %d, %Y")

    context = {
        "teacher": teacher,
        "teacher_name": teacher.user.get_full_name() or teacher.user.username,
        "header_title": site_name,
        "header_subtitle": "Daily Schedule Digest",
        "footer_brand": site_name,
        "date": date_str,
        "target_date": target_date,
        "total_lessons": total_lessons,
        "lessons": lessons_data,
        "timezone": teacher.user.timezone,
        "meeting_link": meeting_link,
        "dashboard_url": dashboard_url,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
    }

    if total_lessons > 0:
        plural_suffix = "s" if total_lessons != 1 else ""
        subject = f"Daily Schedule Digest: {total_lessons} lesson{plural_suffix} scheduled for tomorrow – {site_name}"
    else:
        subject = (
            f"Daily Schedule Digest: No lessons scheduled for tomorrow – {site_name}"
        )

    text_content = render_to_string("booking/emails/teacher_daily_digest.txt", context)
    html_content = render_to_string("booking/emails/teacher_daily_digest.html", context)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[teacher_email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info(
        "Sent daily digest email with %s lessons to teacher %s for date %s.",
        total_lessons,
        teacher_email,
        target_date,
    )
    return True


def _get_teacher_email(booking) -> str:
    return (
        getattr(booking.teacher.user, "email", "")
        or booking.teacher.contact_email
        or ""
    ).strip()


def send_dispute_opened_teacher_email(booking) -> bool:
    """Send branded HTML+TXT alert email to the teacher when a student opens a dispute."""
    teacher_email = _get_teacher_email(booking)
    if not teacher_email:
        logger.info(
            "Teacher %s for booking #%s has no email address; skipping dispute opened email.",
            booking.teacher_id,
            booking.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_name = getattr(settings, "SITE_NAME", "Tutor Booking")
    finance_url = f"{site_url}{reverse('payments:teacher_finance')}"
    local_start = booking.teacher_local_start
    local_end = booking.teacher_local_end
    opened_ts = (
        int(booking.dispute_opened_at.timestamp()) if booking.dispute_opened_at else 0
    )
    dispute_window_days = getattr(settings, "DISPUTE_WINDOW_DAYS", 7)

    return send_dedup_multipart_email(
        recipient_email=teacher_email,
        dedup_key=build_dedup_key(
            f"booking_{booking.id}", f"dispute_opened_{opened_ts}"
        ),
        subject=f"[Dispute Opened] Booking #{booking.id} from {booking.student_display_name} – {site_name}",
        text_template="booking/emails/dispute_opened_teacher.txt",
        html_template="booking/emails/dispute_opened_teacher.html",
        header_subtitle="Dispute Opened",
        headers={"X-Priority": "1", "Importance": "high"},
        extra_context={
            "booking": booking,
            "teacher_name": booking.teacher_display_name,
            "student_name": booking.student_display_name,
            "lesson_type": booking.get_lesson_type_display(),
            "date": local_start.strftime("%A, %B %d, %Y"),
            "time": f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
            "timezone": booking.teacher.user.timezone,
            "amount_display": f"{booking.held_amount:.2f}",
            "currency": "USD",
            "dispute_reason": (booking.dispute_reason or "").strip(),
            "dispute_window_days": dispute_window_days,
            "finance_url": finance_url,
        },
    )


def send_dispute_warning_teacher_email(
    booking, *, warning_day: int, days_remaining: int
) -> bool:
    """Send urgent branded dispute expiration warning email to the teacher on Day 3 or Day 6."""
    teacher_email = _get_teacher_email(booking)
    if not teacher_email:
        logger.info(
            "Teacher %s for booking #%s has no email address; skipping Day %s dispute warning email.",
            booking.teacher_id,
            booking.id,
            warning_day,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    finance_url = f"{site_url}{reverse('payments:teacher_finance')}"
    days_label = (
        f"{days_remaining} day" if days_remaining == 1 else f"{days_remaining} days"
    )
    dispute_window_days = getattr(settings, "DISPUTE_WINDOW_DAYS", 7)
    opened_ts = (
        int(booking.dispute_opened_at.timestamp()) if booking.dispute_opened_at else 0
    )

    return send_dedup_multipart_email(
        recipient_email=teacher_email,
        dedup_key=build_dedup_key(
            f"booking_{booking.id}",
            f"dispute_warning_day{warning_day}_{opened_ts}",
        ),
        subject=(
            f"[Urgent: Day {warning_day} Dispute Warning] "
            f"Booking #{booking.id} auto-refunds in {days_label}"
        ),
        text_template="booking/emails/dispute_warning_teacher.txt",
        html_template="booking/emails/dispute_warning_teacher.html",
        header_subtitle=f"Dispute Day {warning_day} Warning",
        headers={"X-Priority": "1", "Importance": "high"},
        timeout=86400 * dispute_window_days,
        extra_context={
            "booking": booking,
            "teacher_name": booking.teacher_display_name,
            "student_name": booking.student_display_name,
            "warning_day": warning_day,
            "days_remaining": days_remaining,
            "days_label": days_label,
            "amount_display": f"{booking.held_amount:.2f}",
            "currency": "USD",
            "finance_url": finance_url,
        },
    )


class _DisputeResolutionCopy(NamedTuple):
    outcome_label: str
    is_teacher_favor: bool
    student_intro: str
    student_detail: str
    teacher_intro: str
    teacher_detail: str


def _get_dispute_resolution_copy(booking, resolution: str) -> _DisputeResolutionCopy:
    from .models import Booking

    amount_display = f"${booking.held_amount:.2f}"
    copy_map = {
        Booking.DisputeResolution.EXPIRED_AUTO_STUDENT: _DisputeResolutionCopy(
            outcome_label="Auto-Expired in Student Favor",
            is_teacher_favor=False,
            student_intro=(
                f"Your open dispute for Booking #{booking.id} with "
                f"{booking.teacher_display_name} auto-expired after the 7-day review window."
            ),
            student_detail=(
                f"The held amount of {amount_display} has been automatically released back to "
                f"your available wallet balance."
            ),
            teacher_intro=(
                f"The open dispute for Booking #{booking.id} with "
                f"{booking.student_display_name} auto-expired after the 7-day review window."
            ),
            teacher_detail=(
                f"Because the dispute was not resolved within 7 days, the held amount of "
                f"{amount_display} has been automatically released back to "
                f"{booking.student_display_name}'s available wallet balance."
            ),
        ),
        Booking.DisputeResolution.STUDENT_FAVOR: _DisputeResolutionCopy(
            outcome_label="Resolved in Student Favor",
            is_teacher_favor=False,
            student_intro=(
                f"Your dispute for Booking #{booking.id} with "
                f"{booking.teacher_display_name} has been resolved (Resolved in Student Favor)."
            ),
            student_detail=(
                f"The held amount of {amount_display} has been released back to your "
                f"available wallet balance."
            ),
            teacher_intro=(
                f"The dispute for Booking #{booking.id} with "
                f"{booking.student_display_name} has been marked as Resolved in Student Favor."
            ),
            teacher_detail=(
                f"You resolved the dispute in the student's favor. The held amount of "
                f"{amount_display} has been released back to {booking.student_display_name}'s "
                f"available wallet balance."
            ),
        ),
        Booking.DisputeResolution.TEACHER_FAVOR: _DisputeResolutionCopy(
            outcome_label="Resolved in Teacher Favor",
            is_teacher_favor=True,
            student_intro=(
                f"Your dispute for Booking #{booking.id} with "
                f"{booking.teacher_display_name} has been resolved (Resolved in Teacher Favor)."
            ),
            student_detail=(
                f"The dispute was resolved in the teacher's favor. The held amount of "
                f"{amount_display} has been settled for the completed lesson."
            ),
            teacher_intro=(
                f"The dispute for Booking #{booking.id} with "
                f"{booking.student_display_name} has been marked as Resolved in Teacher Favor."
            ),
            teacher_detail=(
                f"You resolved the dispute in your favor. The held amount of "
                f"{amount_display} has been settled as completed lesson revenue."
            ),
        ),
    }
    return copy_map.get(resolution, copy_map[Booking.DisputeResolution.TEACHER_FAVOR])


class _DisputeRecipient(NamedTuple):
    role: str
    email: str
    display_name: str
    intro: str
    detail: str
    cta_label: str
    cta_url: str


def send_dispute_resolved_emails(booking, *, resolution: str) -> int:
    """
    Send deduplicated branded dispute resolution emails to both student and teacher.
    Re-raises transient email errors after attempting both recipients so Celery retries
    can deliver to any failed recipient while keeping succeeded recipients deduplicated.
    """
    from booking.tasks import TRANSIENT_EMAIL_ERRORS, is_transient_email_error

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    copy = _get_dispute_resolution_copy(booking, resolution)
    recipients = [
        _DisputeRecipient(
            role="student",
            email=(getattr(booking.student, "email", "") or "").strip(),
            display_name=booking.student_display_name,
            intro=copy.student_intro,
            detail=copy.student_detail,
            cta_label="View Student Dashboard",
            cta_url=f"{site_url}{reverse('booking:student_dashboard')}",
        ),
        _DisputeRecipient(
            role="teacher",
            email=_get_teacher_email(booking),
            display_name=booking.teacher_display_name,
            intro=copy.teacher_intro,
            detail=copy.teacher_detail,
            cta_label="View Finance Dashboard",
            cta_url=f"{site_url}{reverse('payments:teacher_finance')}",
        ),
    ]

    subject = f"Dispute {copy.outcome_label} – Booking #{booking.id}"
    sent_count = 0
    transient_exc = None

    for recipient in recipients:
        if not recipient.email:
            continue
        dedup_key = build_dedup_key(
            f"booking_{booking.id}",
            f"dispute_{resolution.lower()}_{recipient.role}",
        )
        try:
            dispatched = send_dedup_multipart_email(
                recipient_email=recipient.email,
                dedup_key=dedup_key,
                subject=subject,
                text_template="booking/emails/dispute_resolved.txt",
                html_template="booking/emails/dispute_resolved.html",
                header_subtitle="Dispute Resolution",
                extra_context={
                    "booking": booking,
                    "display_name": recipient.display_name,
                    "outcome_label": copy.outcome_label,
                    "is_teacher_favor": copy.is_teacher_favor,
                    "amount_display": f"{booking.held_amount:.2f}",
                    "currency": "USD",
                    "intro": recipient.intro,
                    "detail": recipient.detail,
                    "cta_label": recipient.cta_label,
                    "cta_url": recipient.cta_url,
                },
            )
            if dispatched:
                sent_count += 1
        except TRANSIENT_EMAIL_ERRORS as exc:
            if is_transient_email_error(exc):
                if transient_exc is None:
                    transient_exc = exc
                logger.warning(
                    "Transient error sending dispute resolved email to %s (%s) for booking #%s; will retry.",
                    recipient.role,
                    recipient.email,
                    booking.id,
                )
            else:
                logger.exception(
                    "Non-transient error sending dispute resolved email to %s (%s) for booking #%s.",
                    recipient.role,
                    recipient.email,
                    booking.id,
                )
        except Exception:
            logger.exception(
                "Failed to send dispute resolved email to %s (%s) for booking #%s.",
                recipient.role,
                recipient.email,
                booking.id,
            )

    if transient_exc is not None:
        raise transient_exc

    return sent_count
