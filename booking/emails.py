import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

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

    site_url = getattr(settings, 'SITE_URL', 'https://englishwithmary.ir').rstrip('/')
    student_dashboard_url = f"{site_url}{reverse('booking:student_dashboard')}"
    local_start = booking.student_local_start
    local_end = booking.student_local_end

    context = {
        'booking': booking,
        'student': booking.student,
        'student_name': booking.student_display_name,
        'teacher_name': booking.teacher_display_name,
        'lesson_type': booking.get_lesson_type_display(),
        'date': local_start.strftime('%A, %B %d, %Y'),
        'time': f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
        'timezone': booking.student.timezone,
        'student_dashboard_url': student_dashboard_url,
        'site_name': 'English with Mary',
        'site_url': site_url,
    }

    subject = f"Lesson Request Received – {context['site_name']}"
    text_content = render_to_string('booking/emails/lesson_requested_student.txt', context)
    html_content = render_to_string('booking/emails/lesson_requested_student.html', context)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[booking.student.email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info("Sent lesson request confirmation email to student %s for booking #%s.", booking.student.email, booking.id)
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

    site_url = getattr(settings, 'SITE_URL', 'https://englishwithmary.ir').rstrip('/')
    dashboard_url = f"{site_url}{reverse('booking:teacher_dashboard')}"
    local_start = booking.teacher_local_start
    local_end = booking.teacher_local_end

    context = {
        'booking': booking,
        'teacher': booking.teacher,
        'teacher_name': booking.teacher_display_name,
        'student_name': booking.student_display_name,
        'student_email': booking.student.email,
        'lesson_type': booking.get_lesson_type_display(),
        'date': local_start.strftime('%A, %B %d, %Y'),
        'time': f"{local_start.strftime('%H:%M')} – {local_end.strftime('%H:%M')}",
        'timezone': booking.teacher.user.timezone,
        'dashboard_url': dashboard_url,
        'site_name': 'English with Mary',
        'site_url': site_url,
    }

    student_display = context['student_name']
    subject = f"New Lesson Request from {student_display} – {context['site_name']}"
    text_content = render_to_string('booking/emails/lesson_requested_teacher.txt', context)
    html_content = render_to_string('booking/emails/lesson_requested_teacher.html', context)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[teacher_email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info("Sent new lesson request alert email to teacher %s for booking #%s.", teacher_email, booking.id)
    return True
