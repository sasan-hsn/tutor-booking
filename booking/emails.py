import logging
from datetime import timezone as dt_timezone
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives, send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

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


def send_dispute_warning_teacher_email(
    booking, *, warning_day: int, days_remaining: int
) -> bool:
    """Send urgent dispute expiration warning email to the teacher on Day 3 or Day 6."""
    teacher_email = booking.teacher.user.email or booking.teacher.contact_email
    if not teacher_email:
        logger.info(
            "Teacher %s for booking #%s has no email address; skipping Day %s dispute warning email.",
            booking.teacher_id,
            booking.id,
            warning_day,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    dashboard_url = f"{site_url}{reverse('booking:teacher_dashboard')}"
    days_label = (
        f"{days_remaining} day" if days_remaining == 1 else f"{days_remaining} days"
    )

    subject = (
        f"[Urgent: Day {warning_day} Dispute Warning] "
        f"Booking #{booking.id} auto-refunds in {days_label}"
    )
    message = (
        f"Hello {booking.teacher_display_name},\n\n"
        f"An open dispute for Booking #{booking.id} with {booking.student_display_name} "
        f"has reached Day {warning_day}.\n\n"
        f"If unresolved, held funds (${booking.held_amount}) will automatically be "
        f"released back to the student's wallet in {days_label}.\n\n"
        f"Please review and resolve this dispute in your Teacher Dashboard:\n"
        f"{dashboard_url}\n"
    )

    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[teacher_email],
        fail_silently=False,
    )
    logger.info(
        "Sent Day %s dispute warning email to teacher %s for booking #%s.",
        warning_day,
        teacher_email,
        booking.id,
    )
    return True


def send_dispute_resolved_emails(booking, *, resolution: str) -> int:
    """Send deduplicated dispute resolution notification emails to both student and teacher."""
    from .models import Booking

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    amount_display = f"${booking.held_amount:.2f}"
    is_student_favor = resolution == Booking.DisputeResolution.STUDENT_FAVOR

    if is_student_favor:
        outcome_label = "Resolved in Student Favor"
        student_detail = (
            f"The held amount of {amount_display} has been released back to your "
            f"available wallet balance."
        )
        teacher_detail = (
            f"You resolved the dispute in the student's favor. The held amount of "
            f"{amount_display} has been released back to {booking.student_display_name}'s "
            f"available wallet balance."
        )
    else:
        outcome_label = "Resolved in Teacher Favor"
        student_detail = (
            f"The dispute was resolved in the teacher's favor. The held amount of "
            f"{amount_display} has been settled for the completed lesson."
        )
        teacher_detail = (
            f"You resolved the dispute in your favor. The held amount of "
            f"{amount_display} has been settled as completed lesson revenue."
        )

    recipients = [
        (
            "student",
            (getattr(booking.student, "email", "") or "").strip(),
            booking.student_display_name,
            (
                f"Your dispute for Booking #{booking.id} with "
                f"{booking.teacher_display_name} has been resolved ({outcome_label})."
            ),
            student_detail,
            f"View your dashboard: {site_url}{reverse('booking:student_dashboard')}",
        ),
        (
            "teacher",
            (
                getattr(booking.teacher.user, "email", "")
                or booking.teacher.contact_email
                or ""
            ).strip(),
            booking.teacher_display_name,
            (
                f"The dispute for Booking #{booking.id} with "
                f"{booking.student_display_name} has been marked as {outcome_label}."
            ),
            teacher_detail,
            f"View Finance dashboard: {site_url}{reverse('payments:teacher_finance')}",
        ),
    ]

    subject = f"Dispute Resolved ({outcome_label}) – Booking #{booking.id}"
    sent_count = 0
    for role, email_addr, display_name, intro, detail, cta in recipients:
        if not email_addr:
            continue
        dedup_key = f"dispute_resolved_email:{booking.id}:{resolution}:{role}"
        if not cache.add(dedup_key, True, timeout=86400):
            continue
        try:
            send_mail(
                subject=subject,
                message=f"Hello {display_name},\n\n{intro}\n\n{detail}\n\n{cta}\n",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email_addr],
                fail_silently=False,
            )
            sent_count += 1
        except Exception:
            cache.delete(dedup_key)
            logger.exception(
                "Failed to send dispute resolved email to %s (%s) for booking #%s.",
                role,
                email_addr,
                booking.id,
            )

    return sent_count
