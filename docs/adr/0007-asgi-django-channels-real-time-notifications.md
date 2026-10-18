# 7. Use ASGI and Django Channels for In-Platform Real-Time Notifications

Date: 2026-10-17

## Status

Accepted

## Context

The platform requires real-time alerting for teachers and students upon critical booking events (such as new lesson requests, confirmations, declines, cancellations, and expirations) without requiring manual page reloads.

While short HTTP polling is simple to implement initially, the project roadmap includes in-platform student-teacher messaging in the immediate next phase. Furthermore, the platform is preparing for multi-tenant scaling where frequent polling across active tabs quickly inflates database load and request overhead.

Transitioning from a pure WSGI monolith to real-time WebSockets requires introducing an ASGI server and a Channel Layer. Deciding this foundation now aligns the in-platform notification system with the upcoming messaging requirements and avoids throwing away a temporary polling subsystem.

## Decision

We will adopt Django Channels and transition the application server to ASGI:

1. **Dedicated `notifications` App**: Keep notification models, consumers, and services isolated from the `booking` app to provide a clean, symmetrical notification backbone for all user types and future features.
2. **Unified ASGI Server**: In production, run Gunicorn using `uvicorn.workers.UvicornWorker` pointing to `tutor_booking.asgi:application` on port 8000. This handles both HTTP and WebSocket connections (`/ws/...`) under a unified multi-worker process model without running split containers or divergent healthchecks.
3. **Redis Channel Layer**: Use `channels_redis.core.RedisChannelLayer` backed by the existing production Redis container (`redis://redis:6379/2` or dedicated database index) to manage user channel groups.
4. **Per-User Authenticated Groups**: Each authenticated user connects via WebSocket to `/ws/notifications/` and joins a private group (`user_<user_id>`). Connections from unauthenticated users are rejected at the handshake.
5. **Data Model Schema (`notifications.Notification`)**:
   - `recipient`: `ForeignKey(User, on_delete=CASCADE, related_name='notifications')`
   - `actor`: `ForeignKey(User, null=True, blank=True, on_delete=SET_NULL, related_name='notifications_triggered')`
   - `event_type`: `CharField(max_length=50, choices=EventType.choices, db_index=True)`
   - `level`: `CharField(max_length=20, choices=[('info', 'Info'), ('success', 'Success'), ('warning', 'Warning')], default='info')`
   - `title`: `CharField(max_length=255)`
   - `message`: `TextField()`
   - `action_url`: `CharField(max_length=500, blank=True)`
   - `metadata`: `JSONField(default=dict, blank=True)`
   - `is_read`: `BooleanField(default=False, db_index=True)`
   - `read_at`: `DateTimeField(null=True, blank=True)`
   - `created_at`: `DateTimeField(auto_now_add=True, db_index=True)`
   - Composite Index: `(recipient, is_read, -created_at)` for instant unread badge counts and fast notification list queries.
6. **Transactional In-App Emission**: The `Notification` record is created synchronously in the primary database transaction to maintain relational consistency. The real-time WebSocket broadcast (`channel_layer.group_send`) is scheduled strictly via `transaction.on_commit()` to prevent phantom notifications if a transaction rolls back.
7. **Read Lifecycle**:
   - Opening the notification dropdown displays the recent 20 notifications without auto-clearing unread state.
   - Clicking an individual notification sends an AJAX request marking it as read (`is_read=True`, `read_at=now()`) and redirects to its `action_url`.
   - An explicit "Mark all as read" button in the dropdown marks all unread notifications for the user as read.
8. **Client Connection & Reconnection Strategy**:
   - Initial page loads render the unread badge count and topbar dropdown items via standard Django context/template tags (or fast API bootstrap).
   - The WebSocket client auto-reconnects with exponential backoff (1s, 2s, 5s, 10s) upon connection drop and sends a sync frame (`{"action": "get_unread_count"}`) upon re-establishing connection.
9. **UI Feedback**: Visual-only Bootstrap toast pops up in the top-right corner when an event arrives during an active session; no audio chime or native OS browser notification permissions required.
10. **Retention Policy**: Retain all notification records indefinitely (no automatic deletion initially), querying the latest 20 in the dropdown. Scheduled Celery pruning can be added later if storage demands it.
11. **Nginx Host Proxy Configuration**: Update host Nginx reverse-proxy configuration to pass `Upgrade` and `Connection "Upgrade"` headers for WebSocket upgrade requests.

## Consequences

### Positive

- Zero database polling overhead from client browsers.
- Real-time event delivery (<50ms) directly to active dashboard sessions.
- Establishes the requisite ASGI and Redis Channel Layer infrastructure needed for upcoming student-teacher messaging.
- Persistent database tracking ensures users have a reliable audit trail of missed notifications across devices.

### Negative

- Transitioning from WSGI to ASGI with Gunicorn Uvicorn workers introduces asynchronous runtime semantics into the application container.
- Nginx configuration on the host VPS requires updating to properly support HTTP `Upgrade` headers for WebSocket connections.
- Automated tests that exercise WebSockets require `channels.testing` utilities and in-memory channel layers.
