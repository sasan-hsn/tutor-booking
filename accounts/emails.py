import logging
from urllib.parse import quote, urlparse
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from .tokens import (
    email_change_revocation_token_generator,
    email_change_token_generator,
    email_verification_token_generator,
)

logger = logging.getLogger(__name__)


def send_verification_email(user, next_url: str | None = None) -> bool:
    """Send email verification link to user."""
    if not user.email:
        logger.info("User #%s has no email address; skipping verification email.", user.pk)
        return False

    site_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000').rstrip('/')
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, 'SITE_NAME', 'English with Mary')

    token = email_verification_token_generator.make_token(user)
    verify_path = reverse('accounts:verify_email', kwargs={'token': token})
    if next_url:
        verify_path += f"?next={quote(next_url)}"
    verification_url = f"{site_url}{verify_path}"

    user_name = user.get_full_name() or user.username

    context = {
        'user': user,
        'user_name': user_name,
        'verification_url': verification_url,
        'header_title': site_name,
        'header_subtitle': "Email Verification",
        'footer_brand': site_name,
        'site_name': site_name,
        'site_url': site_url,
        'site_domain': site_domain,
    }

    subject = f"Verify your email address – {site_name}"
    text_content = render_to_string('accounts/emails/verify_email.txt', context)
    html_content = render_to_string('accounts/emails/verify_email.html', context)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info("Sent verification email to %s for user #%s.", user.email, user.pk)
    return True


def send_email_change_confirmation_email(user) -> bool:
    """Send confirmation email for a pending email change to user.pending_email."""
    if not user.pending_email:
        logger.info("User #%s has no pending_email; skipping confirmation email.", user.pk)
        return False

    site_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000').rstrip('/')
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, 'SITE_NAME', 'English with Mary')

    token = email_change_token_generator.make_token(user, user.pending_email)
    confirm_path = reverse('accounts:confirm_email_change', kwargs={'token': token})
    confirmation_url = f"{site_url}{confirm_path}"

    user_name = user.get_full_name() or user.username

    context = {
        'user': user,
        'user_name': user_name,
        'pending_email': user.pending_email,
        'confirmation_url': confirmation_url,
        'header_title': site_name,
        'header_subtitle': "Email Address Change",
        'footer_brand': site_name,
        'site_name': site_name,
        'site_url': site_url,
        'site_domain': site_domain,
    }

    subject = f"Confirm your new email address – {site_name}"
    text_content = render_to_string('accounts/emails/confirm_email_change.txt', context)
    html_content = render_to_string('accounts/emails/confirm_email_change.html', context)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.pending_email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info("Sent email change confirmation email to %s for user #%s.", user.pending_email, user.pk)
    return True


def send_email_change_advisory_email(user) -> bool:
    """Send security advisory and revocation link to user's current active email."""
    if not user.email or not user.pending_email:
        logger.info("User #%s missing email or pending_email; skipping advisory email.", user.pk)
        return False

    site_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000').rstrip('/')
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, 'SITE_NAME', 'English with Mary')

    token = email_change_revocation_token_generator.make_token(user, user.pending_email)
    revoke_path = reverse('accounts:revoke_email_change', kwargs={'token': token})
    revocation_url = f"{site_url}{revoke_path}"

    user_name = user.get_full_name() or user.username

    context = {
        'user': user,
        'user_name': user_name,
        'current_email': user.email,
        'pending_email': user.pending_email,
        'revocation_url': revocation_url,
        'header_title': site_name,
        'header_subtitle': "Security Alert",
        'footer_brand': site_name,
        'site_name': site_name,
        'site_url': site_url,
        'site_domain': site_domain,
    }

    subject = f"Security Alert: Email address change requested – {site_name}"
    text_content = render_to_string('accounts/emails/email_change_advisory.txt', context)
    html_content = render_to_string('accounts/emails/email_change_advisory.html', context)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info("Sent email change advisory email to %s for user #%s.", user.email, user.pk)
    return True
