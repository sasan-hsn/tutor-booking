Hello {{ recipient_name }},

You have {{ unread_count }} unread message{{ unread_count|pluralize }} from {{ sender_name }}:

{% for message in unread_messages %}
- {{ message.text|truncatechars:300 }}
{% endfor %}

To view and reply to these messages, visit your conversation at:
{{ conversation_url }}

Best regards,
{{ site_name }}
{{ site_url }}
