# Feature Specification: Role-Aware Messaging Navigation, Tenant Entry Points, and Split-View Inbox

## Problem Statement

Following the completion of the core messaging and real-time engine, participants have no direct graphical user interface entry points to initiate or access conversations outside of automated booking lifecycle events. Prospective students browsing a teacher's workspace who have questions before booking cannot initiate contact from their dashboard. Enrolled students and teachers viewing upcoming or past lessons cannot quickly message their counterpart without navigating away and manually searching their message history. In the teacher's student roster, there is no shortcut to message an enrolled learner. Furthermore, the existing inbox presents a generic conversation list that forces a single-tenant student into an unnecessary list-view navigation step, while providing teachers with no split-view workspace to efficiently triage and converse with multiple learners.

## Solution

Deliver a cohesive, tenant-scoped navigation and inbox experience tailored to the distinct operational needs of students and teachers within an isolated workspace:

1. **Bidirectional Route Initiation**: Generalize conversation initiation to accept either counterpart username, validating role eligibility and tenant boundaries while idempotently resolving or creating the underlying conversation thread.
2. **Role-Aware Inbox Experience**:
   - For students (who only ever interact with their single workspace teacher), routing directly into the full-width conversation thread, eliminating redundant list selection.
   - For teachers (who interact with many students), providing a responsive two-pane split-view on desktop (conversation list on the left, active timeline or selection placeholder on the right) with clean single-column drill-down on mobile.
3. **Prospective Student Inquiry Entry Points**: Support starting/opening conversations with the teacher directly from the Messages flow for learners without active threads, coupled with an inviting empty state inside the conversation timeline to guide their first question, keeping `student_dashboard.html` clean and uncluttered.
4. **Contextual Booking Shortcuts**: Place a direct message action trigger inside the lesson detail modal for both student and teacher roles, deep-linking directly to their thread, while keeping upcoming and past lesson cards clean and uncluttered.
5. **Teacher Roster Action Shortcut**: Provide a direct message action trigger in the teacher's student management table for every enrolled student.
6. **Live Multi-Thread Coordination**: Keep the teacher's inbox list synchronized in real time via the notification stream, dynamically reordering conversations, updating message snippets, and refreshing unread badges without full page reloads.

## User Stories

1. As a prospective student who just registered in a teacher's workspace, I want a clean and uncluttered student dashboard focused on upcoming and past lessons, with messaging accessible via dedicated message flows.
2. As a prospective student with zero conversations started, I want an inviting prompt on `/messages/` that invites me to message my teacher, so that I feel welcomed and know where to get answers before booking.
3. As a prospective student opening an empty conversation timeline, I want to see clear guidance encouraging me to send my first question before booking.
4. As an enrolled student who has booked lessons, I want the dashboard to remain clean and focused on lesson management without intrusive inquiry banners.
5. As an authenticated student navigating to the messages area via the sidebar or topbar, I want to be taken directly into my conversation with my teacher, so that I do not have to click through a redundant one-item conversation list.
6. As an authenticated student with zero messages sent, I want to see an inviting empty state in my direct conversation view, so that I understand I can type a message to start our discussion.
7. As an enrolled student viewing upcoming or past lessons on my dashboard, I want dashboard cards to remain clean and uncluttered, with direct communication accessible via the lesson detail modal.
8. As an enrolled student inspecting a lesson in the lesson details modal, I want a message button inside the modal, so that I can communicate with my teacher without leaving the modal context manually.
9. As a teacher viewing my student management roster, I want a message shortcut icon next to each enrolled student, so that I can quickly reach out to any student I teach.
10. As a teacher navigating to my messages area, I want a responsive two-pane split-view on desktop, so that I can view my list of conversations on the left while reading and replying to messages on the right.
11. As a teacher accessing the messages area without selecting a specific thread, I want the right pane to show a clean placeholder prompt to select a conversation, so that my read watermark is not prematurely advanced for threads I have not deliberately opened.
12. As a teacher clicking an enrolled or prospective student thread in the split-view list, I want the timeline and composer to load cleanly in the right pane, so that I can respond immediately.
13. As a teacher viewing my messages on a mobile device, I want a single-column drill-down experience where the conversation list fills the screen, and tapping a conversation opens the full timeline with a clear back button, so that the interface is comfortable on small screens.
14. As a teacher inspecting a lesson in the lesson details modal, I want a direct message button linking to that student's thread, so that I can coordinate directly from my teaching schedule.
15. As a teacher actively viewing a conversation in the split-view right pane, I want incoming messages from other students to bubble their conversation item to the top of the left list with an unread badge and updated snippet in real time, so that I stay informed of incoming inquiries without refreshing.
16. As an authenticated student attempting to initiate a conversation with another student, I want the platform to reject the request with an access forbidden response, so that student privacy and tenant boundaries are strictly preserved.
17. As an unverified student attempting to initiate a conversation, I want to be prompted to verify my email address, so that teachers are protected against automated spam.
18. As a teacher, I want prospective student inquiries from registered learners with zero bookings to appear cleanly in my split-view inbox, so that I can convert interested students into bookings without cluttering my enrolled student management table.

## Implementation Decisions

- **Bidirectional Initiation Endpoint**:
  - The conversation initiation endpoint will accept a target username parameter and dynamically evaluate the authenticated caller's role against the target user's role.
  - If caller is Student: Target must be an active, verified Teacher belonging to the tenant workspace. The caller must have a verified email address.
  - If caller is Teacher: Target must be a registered Student belonging to the tenant workspace.
  - Calling this endpoint executes the idempotent conversation resolution service and returns a redirect response to the specific conversation timeline URL.
  - Non-existent target users return a standard 404 response. Mismatched roles or unauthorized peer relationships return a 403 forbidden response.

- **Role-Aware Inbox Dispatcher**:
  - The base messages URL dispatcher will inspect the authenticated user's role:
    - When accessed by a Student: Resolve the conversation associated with the tenant's teacher. If the thread does not yet exist, initialize it idempotently. Render the dedicated single-pane full-width conversation timeline template.
    - When accessed by a Teacher: Query all active conversations for the teacher ordered by descending activity timestamp. If accessed without a specific thread identifier, render the two-pane split-view template with a neutral "Select a conversation to start messaging" placeholder in the detail pane. If accessed with a thread identifier, render the split-view with that conversation marked active in the list and its timeline loaded in the detail pane.

- **Responsive Split-View Structure**:
  - On desktop viewports (medium breakpoint and above), the teacher inbox renders a unified two-column layout:
    - Left column (fixed percentage width, scrollable): Lists conversation items with counterpart avatar, display name, relative timestamp, last message snippet, and unread badge.
    - Right column (flex auto-fill): Renders the active conversation header, scrollable timeline, booking context drawer, and message composer.
  - On mobile viewports (below medium breakpoint):
    - The base messages URL renders only the full-width conversation list.
    - The conversation detail URL renders only the full-width timeline, presenting a back navigation link in the top bar leading back to the conversation list.
  - Conversation selection in the desktop list uses standard server-rendered anchor links to guarantee robust history, bookmarking, and link sharing without single-page application router complexity.

- **Prospective Student Messages Flow & Empty State**:
  - In `inbox_view` (`/messages/`): When a student has no existing conversations, the view resolves the tenant teacher and renders an inviting empty state prompting the student with *"Have questions before booking?"* and providing an action CTA linking to `/messages/with/<teacher_username>/`.
  - When a student navigates to an empty conversation timeline (`/messages/<conversation_id>/`), the timeline renders an inviting empty state encouraging them to ask their first question before booking.
  - Keeps `student_dashboard.html` clean and uncluttered without multi-state inquiry cards.

- **Lesson Detail Modal Action Integration**:
  - Following UI/UX refinement to keep dashboard cards clean and uncluttered, the booking message shortcut lives exclusively inside the lesson detail modal.
  - The modal header includes a secondary action button featuring a message icon (`bi-chat-dots`) and "Message" label.
  - The action button links directly to the initiation endpoint passing the counterpart's username (with a null-safe guard for unbooked slots on teacher view).
  - Clicking this button transitions the user directly to the conversation timeline with the input composer focused and timeline scrolled to the latest message.

- **Teacher Student Management Roster Shortcut**:
  - Add an action column to the student management table with an icon button linking to the initiation endpoint with the target student's username.
  - The underlying query for this table remains restricted to students with booking history (`CONFIRMED` or `COMPLETED`), preserving its focus as an enrolled student roster while leaving pre-booking inquiries to the messages inbox.

- **Notification Stream Metadata Enhancement**:
  - Enhance the real-time notification broadcast dispatched over the user's private notification channel upon message submission to include structured conversation metadata: conversation identifier, counterpart display name, updated message snippet, and updated total unread count.
  - The client-side dashboard notification listener will inspect incoming message events: if currently viewing the teacher split-view inbox, it will dynamically locate or prepend the matching conversation list item, update its preview snippet and timestamp, increment its conversation unread counter, and move the item to the top of the left pane.

## Testing Decisions

- **Definition of a Good Test**:
  - Tests must verify externally observable behavior at system boundaries (HTTP request/response codes, redirect targets, rendered DOM structures, role authorization checks, and WebSocket channel broadcast payloads). Tests must not inspect internal private helper methods or ephemeral local variables.
- **Modules to Test**:
  - **Initiation Endpoint**: Test student-to-teacher initiation, teacher-to-student initiation, student-to-student prohibition (403), unverified email gating (403), self-messaging prevention, and non-existent counterpart handling (404).
  - **Role-Aware Inbox Routing**: Test that students accessing `/messages/` receive the direct single-pane view, and teachers accessing `/messages/` receive the split-view inbox with selection placeholder.
  - **Prospective Student Inquiry Flow**: Test that a student without an existing conversation visiting `/messages/` sees the prompt and action link to start a conversation with the teacher; that `GET /messages/with/<teacher_username>/` idempotently resolves/creates the conversation and redirects to the conversation timeline; and that `GET /messages/<conversation_id>/` renders the inviting empty state guidance for prospective students.
  - **Lesson Detail Modal Links & Clean Cards**: Test that rendered upcoming/past lesson cards do not render message buttons (preserving clean cards), and verify that the lesson detail modal contains the message action button with valid reverse URLs across roles and lesson states.
  - **Teacher Student Management**: Test that the roster table renders the message shortcut link for enrolled students.
  - **Real-Time Notification Payload**: Test that submitting a message broadcasts the enhanced metadata dictionary over the recipient's notification group.
- **Prior Art**:
  - `chat/tests/test_views.py`, `chat/tests/test_services.py`, `chat/tests/test_consumers.py`, `booking/tests/test_views.py`, and `booking/tests/test_access_control.py`.

## Out of Scope

- Cross-tenant or marketplace teacher discovery directories.
- Unauthenticated public landing page chat widgets or live chat popups.
- Floating bottom-right chat overlay widgets.
- File or image attachment uploads within chat messages.
- Group messaging or multi-party conversation threads.
- Audio or video calling directly embedded inside the chat timeline.

## Further Notes

- Fully conforms to ADR 0008 (Messaging System Architecture) and ADR 0009 (Role-Aware Messaging Navigation and Split-View UX).
- Maintains strict tenant isolation suitable for independent tutor SaaS expansion.
