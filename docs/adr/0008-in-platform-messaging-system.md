# 8. In-Platform Student-Teacher Messaging System Architecture

Date: 2026-10-29

## Status

Accepted

## Context

Following the completion of the CS50W capstone milestone and the adoption of ASGI and Django Channels in ADR 0007, the platform requires an in-platform direct messaging capability between students and teachers.

Prospective students need to communicate with instructors prior to booking trial lessons (e.g. discussing learning goals, assessing language proficiency levels, verifying scheduling nuances), and active students need a dedicated channel for lesson logistics, homework follow-ups, and class coordination.

While the application currently operates as a single-teacher platform for English tutoring, the architecture must support multi-tenant scaling from day one (a platform for independent tutors) without requiring schema rewrites, constraint changes, or migration churn.

Building a secure, scalable, and responsive messaging experience requires addressing several core challenges:
1. Preventing duplicate threads and ensuring strict multi-tenant isolation.
2. Managing real-time message delivery and ephemeral presence (typing indicators, read receipts) without database write amplification.
3. Providing reliable offline alerting without spamming participants during rapid conversational exchanges.
4. Unifying booking lifecycle events with conversational history without tight coupling between domain modules.
5. Guaranteeing abuse prevention, rate limiting, and XSS safety on all exchanged content.

## Decision

We will implement a dedicated Django application (`chat`) operating on top of the established ASGI/Django Channels foundation and Celery background workers:

### 1. Data Schema & Multi-Tenant Scoping (`chat.models`)

#### `Conversation` Model
- `id`: `UUIDField(primary_key=True, default=uuid.uuid4, editable=False)` to prevent sequential identifier enumeration.
- `student`: `ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='student_conversations')`
- `teacher`: `ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='teacher_conversations')`
- `student_last_read_message_id`: `BigIntegerField(default=0)`
- `teacher_last_read_message_id`: `BigIntegerField(default=0)`
- `last_message_at`: `DateTimeField(null=True, blank=True, db_index=True)`
- `last_message_snippet`: `CharField(max_length=120, blank=True)`
- `created_at`: `DateTimeField(auto_now_add=True)`
- `updated_at`: `DateTimeField(auto_now=True)`

**Constraints & Invariants**:
- Composite Unique Constraint: `UniqueConstraint(fields=['student', 'teacher'], name='unique_student_teacher_conversation')` guarantees exactly one persistent thread per pair across the platform.
- Check Constraint: `CheckConstraint(condition=~models.Q(student=models.F('teacher')), name='prevent_self_conversation')`.
- Clean Validation: Enforce `student.role == 'student'` and `teacher.role == 'teacher'`.

#### `Message` Model
- `id`: `BigAutoField(primary_key=True)` (monotonically increasing integer used for cursors and watermarks).
- `conversation`: `ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')`
- `sender`: `ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_messages')`
- `message_type`: `CharField(max_length=20, choices=[('text', 'Text'), ('system_event', 'System Event')], default='text')`
- `text`: `TextField(max_length=2000, blank=True)`
- `metadata`: `JSONField(default=dict, blank=True)` (stores structured booking context: `booking_id`, `event_type`, `snapshot`)
- `created_at`: `DateTimeField(auto_now_add=True, db_index=True)`

**Indexing**:
- Composite Index: `(conversation, id)` and `(conversation, created_at)` for high-throughput cursor pagination and unread watermark filtering.

### 2. Monotonic Watermark Read Tracking

Read status is tracked via conversation-level participant watermarks (`student_last_read_message_id` and `teacher_last_read_message_id`) rather than per-message boolean flags:
- Updating read status is an $O(1)$ indexed query: `Conversation.objects.filter(id=c_id).update(student_last_read_message_id=Greatest(F('student_last_read_message_id'), new_id))`.
- Strictly monotonic updates prevent loading older scrollback messages from accidentally moving the watermark backward.
- Unread message count per conversation is evaluated as:
  `messages.filter(id__gt=watermark).exclude(sender=user).count()`
- Eliminates row-level locking, deadlocks, and index churn when clearing large backlogs of unread messages.

### 3. Ingestion & Real-Time WebSocket Topology (Hybrid Model)

#### HTTP POST Ingestion
- Clients submit outgoing messages via standard authenticated AJAX/Fetch HTTP POST to `/messages/<uuid:conversation_id>/send/`.
- Leverages Django's native session authentication, CSRF token validation, form/serializer validation, and `django-ratelimit` defense.
- Database write occurs inside `transaction.atomic()`, which updates `Message`, advances the sender's watermark, and updates `Conversation.last_message_at` and `last_message_snippet`.
- Real-time broadcast to the Channels group (`chat_<conversation_id>`) is scheduled strictly inside `transaction.on_commit()` to prevent phantom messages on transaction rollback.

#### WebSocket Room Streaming (`/ws/chat/<uuid:conversation_id>/`)
- Authenticated `ChatConsumer` connects users to the conversation's room group (`chat_<conversation_id>`). Handshake verifies that `scope["user"]` is either `conversation.student` or `conversation.teacher`.
- WebSockets handle downstream real-time streaming of new messages, read receipts (`read_receipt` broadcast), and typing indicators (`user_typing` broadcast).
- Read receipts sent from the client advance the participant's watermark in the database and broadcast the new watermark to the counterpart.
- Keystroke typing signals are rate-limited server-side (max 1 frame per 3s per user) and forwarded strictly in-memory over the Redis channel layer (`group_send`) without database writes. Clients auto-clear counterpart typing indicators after 3.5s.

#### Global Unread Badge Streaming (`/ws/notifications/`)
- When a new message is committed, the service layer evaluates the recipient's total unread message count across all active conversations.
- It broadcasts `{"type": "unread_chat_count", "count": total_count}` to the user's private notification group (`user_<recipient_id>`).
- The existing topbar envelope icon (`bi-chat-dots`) updates dynamically across all open browser tabs without opening additional WebSocket connections.

### 4. Decoupled Booking Anchors & Service Layer

- Booking events (`booking_requested`, `booking_confirmed`, `cancellation_requested`, `booking_cancelled`) emit system messages via an explicit `chat.services.record_booking_event(booking, event_type)` service layer call inside the booking action transactions (`transaction.on_commit()`).
- No implicit Django post-save signals are used.
- System messages store structured JSON payloads (booking ID, lesson type, start/end timestamps, action URLs) instead of pre-rendered strings.
- In the Conversation Inbox, system messages render clean human-readable snippets in `last_message_snippet` (e.g. `📅 Lesson Request submitted for Oct 30`).

### 5. Smart Debounced Offline Email Digest via Celery

To prevent email spam during active back-and-forth chat while ensuring offline users never miss critical communications:
1. When a new message arrives from User A to User B:
   - Check an atomic Redis cache flag: `chat_digest_pending:{recipient_id}:{conversation_id}`.
   - If not set, set the flag with a 15-minute TTL (`timeout=900`) and enqueue `send_unread_message_digest.apply_async(args=[conversation_id, recipient_id], countdown=900)`.
   - Subsequent messages sent during this 15-minute window find the flag active and skip scheduling redundant tasks.
2. When the recipient views the conversation and advances their watermark, the cache key is cleared immediately.
3. When the Celery task executes at $T+15\text{ min}$:
   - Re-checks the database: if `messages.filter(id__gt=watermark).exclude(sender=recipient).exists()` is false, exit silently.
   - Re-checks active room presence: if recipient has an active WebSocket connection, exit silently.
   - If unread messages remain and user is offline, compose a consolidated email digest summarizing pending messages with a direct deep-link to the conversation.

### 6. Timeline Rendering, History Pagination & URL Routing

- **App Name**: `chat`
- **Endpoints**:
  - `GET /messages/`: Conversation Inbox. Lists active conversations (`last_message_at__isnull=False`) sorted by `-last_message_at`.
  - `GET /messages/<uuid:conversation_id>/`: Main conversation view. Server-renders the latest 30 messages directly into the Django template to guarantee fast, zero-flicker initial load.
  - `GET /messages/<uuid:conversation_id>/history/?before=<bigint:message_id>`: Cursor-based reverse pagination returning the prior 30 messages as JSON for seamless upward infinite scroll.
  - `POST /messages/<uuid:conversation_id>/send/`: HTTP POST message submission.
  - `GET /messages/with/<str:teacher_username>/`: Idempotent entrypoint. Validates target user as an active, verified teacher; executes `Conversation.objects.get_or_create(student=request.user, teacher=teacher)` inside `transaction.atomic()`; redirects (`302`) to `/messages/<conversation_id>/`.

### 7. Content Security & Input Sanitization

- Text length capped at 2,000 characters; whitespace-only messages rejected.
- All user content is escaped through Django's template engine.
- Linkification is executed via a safe auto-linking utility that:
  - Whitelists only `http://` and `https://` protocols (rejecting `javascript:`, `data:`, etc.).
  - Enforces `rel="noopener noreferrer"` and `target="_blank"` on all rendered anchors to guard against reverse tab-nabbing.
- Endpoint protected by `django-ratelimit` (sliding window of 30 messages per minute per user/IP).

## Consequences

### Positive

- **Multi-Tenant Scalability**: Fully isolated `(student, teacher)` scoping supports a multi-tutor marketplace without data model or query refactoring.
- **High-Performance Read Tracking**: Monotonic integer watermarks reduce database write volume and index contention by orders of magnitude compared to per-message booleans.
- **Zero WebSocket Message Loss**: Ingesting messages via HTTP POST provides battle-tested CSRF protection, transactional rollbacks, and standard error handling.
- **Noise-Free Email Alerts**: 15-minute debounced Celery digests eliminate email notification fatigue during active chat sessions.
- **Unified Booking Context**: Timeline booking anchors and header drawers provide clear chronological context between students and instructors.

### Negative

- **Dual-Endpoint Client State**: The client JavaScript manages both the global notification WebSocket (`/ws/notifications/`) and the conversation room socket (`/ws/chat/<uuid:conversation_id>/`).
- **Denormalization Maintenance**: `last_message_at` and `last_message_snippet` on `Conversation` require disciplined updates inside the central `chat.services` layer to prevent desynchronization.
- **Testing Requirements**: Comprehensive integration testing requires simulating Channels WebSocket frames, HTTP POST message submissions, and asynchronous Celery tasks simultaneously.
