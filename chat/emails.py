import logging
from urllib.parse import urlparse
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)


def send_chat_unread_digest_email(
    recipient,
    sender,
    conversation,
    unread_messages,
) -> bool:
    """
    Sends a consolidated email alert for unread chat messages to an offline recipient.
    """
    if not recipient.email:
        logger.info(
            "Recipient %s has no email address; skipping unread message digest email.",
            recipient.id,
        )
        return False

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    site_domain = urlparse(site_url).netloc or site_url
    site_name = getattr(settings, "SITE_NAME", "English with Mary")

    conversation_path = reverse(
        "chat:conversation_detail",
        kwargs={"conversation_id": conversation.id},
    )
    conversation_url = f"{site_url}{conversation_path}"

    recipient_name = recipient.get_full_name() or recipient.username
    sender_name = sender.get_full_name() or sender.username
    unread_count = len(unread_messages)

    if unread_count == 1:
        subject = f"New message from {sender_name}"
    else:
        subject = f"{unread_count} new messages from {sender_name}"

    context = {
        "recipient": recipient,
        "recipient_name": recipient_name,
        "sender": sender,
        "sender_name": sender_name,
        "unread_count": unread_count,
        "unread_messages": unread_messages,
        "conversation": conversation,
        "conversation_url": conversation_url,
        "header_title": site_name,
        "header_subtitle": "Direct Message Digest",
        "footer_brand": site_name,
        "site_name": site_name,
        "site_url": site_url,
        "site_domain": site_domain,
    }

    text_content = render_to_string("chat/emails/unread_message_digest.txt", context)
    html_content = render_to_string("chat/emails/unread_message_digest.html", context)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient.email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send(fail_silently=False)
    logger.info(
        "Sent unread message digest to %s for conversation %s (%s unread messages).",
        recipient.email,
        conversation.id,
        unread_count,
    )
    return True
