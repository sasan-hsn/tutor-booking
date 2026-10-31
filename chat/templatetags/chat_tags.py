from django import template
from chat.utils import linkify_message_text

register = template.Library()

register.filter("chat_linkify", linkify_message_text)
