# 9. Role-Aware Messaging Navigation, Tenant-Scoped Entry Points, and Split-View UX

Date: 2026-11-18

## Status

Accepted

## Context

Following the core implementation of the `chat` engine (ADR 0008), the messaging system required clear user entry points and an intuitive inbox UX. The platform operates as an isolated tenant SaaS (Shopify/Teachable model for independent tutors) rather than a cross-tutor marketplace. We needed to define how prospective and enrolled students connect with their teacher, how teachers initiate conversations from student rosters, and whether to use full-page split-views, floating chat widgets, or direct-thread navigation.

## Decision

1. **Role-Aware Inbox Architecture (`/messages/`)**:
   - **For Teachers**: Desktop viewports render a 2-pane split-view (left scrollable conversation list, right active timeline or empty placeholder before selection to prevent premature watermark advancement). Mobile viewports render single-column drill-down. Navigation between conversations uses standard server-rendered links (`/messages/<uuid:conversation_id>/`).
   - **For Students**: Because each student belongs to an isolated teacher workspace and only ever communicates with that single teacher, `/messages/` bypasses the redundant 1-item conversation list and renders the full-width single-pane chat view directly.
2. **Public Landing Page Boundary**:
   - The public landing page remains dedicated to teacher presentation and booking/signup CTAs; no chat widgets or landing page message triggers exist.
3. **Student Dashboard & Prospective Inquiry Entry Points**:
   - *Initial Concept*: Proposed a multi-state inquiry card on `student_dashboard.html` across onboarding, active, and retired lifecycle states.
   - *Decision & Simplification*: To keep `student_dashboard.html` clean and uncluttered (matching the modal-only booking shortcut decision in section 4), prospective inquiry cards are omitted from the dashboard. Instead, prospective students without an existing conversation initiate inquiries directly from the dedicated Messages flow (`/messages/`) via a clear action prompt (*"Have questions before booking? Message Teacher"*) linking to `/messages/with/<teacher_username>/`, and an inviting empty state inside the conversation timeline provides guidance to send their first question before booking.
4. **Booking-Anchored Entry Points**:
   - To keep dashboard upcoming and past lesson cards clean and uncluttered, booking-level message shortcuts live exclusively inside the Lesson Detail modal. The modal features a secondary "Message" action button (`bi-chat-dots`) linking via `{% url 'chat:start_conversation' counterpart.username %}`, landing at the bottom of the timeline with the composer focused.
5. **Bidirectional Initiation & Teacher Roster**:
   - `/messages/with/<str:username>/` is generalized to support bidirectional role validation.
   - In `teacher_student_management.html`, a "Message Student" shortcut icon is added to each row. The roster remains strictly for enrolled students with booking history; prospective inquiries with zero bookings live exclusively inside `/messages/`.
6. **Real-Time Notification Coordination**:
   - The global `/ws/notifications/` WebSocket delivers unread counts and inbox metadata (`conversation_id`, `snippet`, `unread_count`, `sender_name`, `timestamp`) to reorder and update the teacher's inbox list live.
   - Room-specific WebSockets (`/ws/chat/<uuid:conversation_id>/`) are connected dynamically only to the active right-pane thread.
   - Floating chat popups are explicitly rejected to eliminate layout collisions, modal conflicts, and state overhead.

## Consequences

- **Clear Information Architecture**: Students get zero friction directly into their single teacher thread, while teachers get a scalable split-view inbox for multi-student management.
- **Strict Tenant Isolation**: Prevents unsolicited cross-tenant spam while ensuring prospective students can ask questions before purchasing.
- **Maintainable Frontend**: Avoids client-side SPA complexity and floating widget state synchronization across multi-page Django navigations.
