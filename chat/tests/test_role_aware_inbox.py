from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from chat.models import Conversation
from chat.services import send_message

User = get_user_model()


class RoleAwareInboxStudentTests(TestCase):
    """
    Seam 1: Student Direct Inbox Routing (GET /messages/ as Student)
    Verifies that students accessing /messages/ bypass the redundant 1-item conversation list,
    resolving or creating their conversation directly, displaying the full-width timeline,
    enforcing email verification for initial creation, and rendering #274 empty states.
    """

    def setUp(self):
        cache.clear()
        self.client = Client()

        self.teacher_user = User.objects.create_user(
            username="teacher_jane",
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
            password="testpassword123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher_profile = self.teacher_user.teacher_profile

        self.verified_student = User.objects.create_user(
            username="student_alice",
            first_name="Alice",
            last_name="Smith",
            email="alice@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

        self.unverified_student = User.objects.create_user(
            username="student_bob",
            first_name="Bob",
            last_name="Jones",
            email="bob@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=False,
        )

    def tearDown(self):
        cache.clear()

    def test_student_with_existing_conversation_renders_direct_detail_view(self):
        """
        A verified student accessing /messages/ with an existing conversation directly receives
        the full-width single-pane conversation view with their teacher, bypassing the inbox list.
        """
        conv = Conversation.objects.create(
            student=self.verified_student,
            teacher=self.teacher_user,
        )
        msg = send_message(conv, sender=self.teacher_user, text="Hello Alice!")

        self.client.force_login(self.verified_student)
        response = self.client.get(reverse("chat:inbox"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chat/conversation_detail.html")
        self.assertTemplateNotUsed(response, "chat/inbox.html")
        self.assertEqual(response.context["conversation"].id, conv.id)
        self.assertEqual(response.context["counterpart"], self.teacher_user)
        self.assertTrue(response.context["is_student"])
        self.assertContains(response, "Hello Alice!")

        # Watermark is advanced
        conv.refresh_from_db()
        self.assertEqual(conv.student_last_read_message_id, msg.id)

    def test_student_without_conversation_creates_conversation_and_renders_empty_timeline(
        self,
    ):
        """
        A verified student visiting /messages/ with zero existing conversations idempotently creates
        the conversation with the teacher and renders the empty timeline with #274 prospective guidance.
        """
        self.assertEqual(
            Conversation.objects.filter(
                student=self.verified_student,
                teacher=self.teacher_user,
            ).count(),
            0,
        )

        self.client.force_login(self.verified_student)
        response = self.client.get(reverse("chat:inbox"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chat/conversation_detail.html")
        self.assertTemplateNotUsed(response, "chat/inbox.html")

        # Conversation was created
        conv = Conversation.objects.filter(
            student=self.verified_student,
            teacher=self.teacher_user,
        ).first()
        self.assertIsNotNone(conv)
        self.assertEqual(response.context["conversation"].id, conv.id)
        self.assertTrue(response.context["is_prospective_student"])

        # Timeline empty state rendered with #274 prospective guidance
        self.assertContains(response, 'id="chatEmptyState"')
        self.assertContains(response, "Have questions before booking?")
        self.assertContains(response, "Send your first message below")

    def test_unverified_student_without_conversation_blocked_by_verification_gate(self):
        """
        Edge case 1: An unverified student (is_email_verified=False) visiting /messages/
        with no existing conversation is blocked with 403 and the unverified prompt template.
        """
        self.client.force_login(self.unverified_student)
        response = self.client.get(reverse("chat:inbox"))

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "chat/unverified_prompt.html")
        self.assertEqual(
            response.context.get("teacher_username"), self.teacher_user.username
        )

        # Conversation is NOT created
        self.assertFalse(
            Conversation.objects.filter(
                student=self.unverified_student,
                teacher=self.teacher_user,
            ).exists()
        )

    def test_unverified_student_with_existing_conversation_can_view_timeline(self):
        """
        An unverified student who already has a conversation can view the timeline on /messages/,
        with the email verification alert displayed.
        """
        conv = Conversation.objects.create(
            student=self.unverified_student,
            teacher=self.teacher_user,
        )
        send_message(conv, sender=self.teacher_user, text="Welcome!")

        self.client.force_login(self.unverified_student)
        response = self.client.get(reverse("chat:inbox"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chat/conversation_detail.html")
        self.assertContains(response, "Welcome!")
        self.assertContains(
            response, "You must verify your email address before sending messages."
        )


class RoleAwareInboxTeacherTests(TestCase):
    """
    Seams 2 & 3: Teacher Inbox Split-View & Active Thread
    Verifies that a teacher accessing /messages/ receives the 2-pane split-view on desktop
    with the selection placeholder, mobile view showing full-width list, unread counts preserved,
    and /messages/<conversation_id>/ loading the active thread, highlighting it in the list,
    clearing its unread count, and providing a back button on mobile.
    """

    def setUp(self):
        cache.clear()
        self.client = Client()

        self.teacher_user = User.objects.create_user(
            username="teacher_jane",
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
            password="testpassword123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher_profile = self.teacher_user.teacher_profile

        self.student_a = User.objects.create_user(
            username="student_alice",
            first_name="Alice",
            last_name="Smith",
            email="alice@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

        self.student_b = User.objects.create_user(
            username="student_bob",
            first_name="Bob",
            last_name="Jones",
            email="bob@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

        # Alice's conversation: 1 message earlier
        self.conv_a = Conversation.objects.create(
            student=self.student_a,
            teacher=self.teacher_user,
        )
        self.msg_a1 = send_message(
            self.conv_a, sender=self.student_a, text="Hi from Alice"
        )

        # Bob's conversation: 2 messages later
        self.conv_b = Conversation.objects.create(
            student=self.student_b,
            teacher=self.teacher_user,
        )
        self.msg_b1 = send_message(
            self.conv_b, sender=self.student_b, text="First msg from Bob"
        )
        self.msg_b2 = send_message(
            self.conv_b, sender=self.student_b, text="Second msg from Bob"
        )

    def tearDown(self):
        cache.clear()

    def test_teacher_inbox_renders_split_view_with_placeholder(self):
        """
        Seam 2: Teacher accessing /messages/ receives 2-pane split-view on desktop,
        with conversation list on the left and selection placeholder on the right.
        No watermarks are advanced, unread indicators are preserved.
        """
        self.client.force_login(self.teacher_user)
        response = self.client.get(reverse("chat:inbox"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chat/inbox.html")

        # Split layout containers
        self.assertContains(response, "chat-split-layout")
        self.assertContains(response, "chat-split-sidebar")
        self.assertContains(response, "chat-split-main")
        self.assertContains(response, "split_view_inbox.js")

        # Selection placeholder present in right pane
        self.assertContains(response, "chat-placeholder-pane")
        self.assertContains(response, "Select a conversation to start messaging")

        # Sidebar list ordering: Bob first (more recent), then Alice
        conversations = response.context["conversations"]
        self.assertEqual(len(conversations), 2)
        self.assertEqual(conversations[0]["conversation"].id, self.conv_b.id)
        self.assertEqual(conversations[1]["conversation"].id, self.conv_a.id)

        # Unread badges preserved: Bob has 2, Alice has 1
        self.assertEqual(conversations[0]["unread_count"], 2)
        self.assertEqual(conversations[1]["unread_count"], 1)

        # Mobile viewport class: placeholder is hidden on mobile (d-none d-md-flex)
        self.assertContains(response, "chat-placeholder-pane")

        # Watermarks NOT advanced (remain 0)
        self.conv_a.refresh_from_db()
        self.conv_b.refresh_from_db()
        self.assertEqual(self.conv_a.teacher_last_read_message_id, 0)
        self.assertEqual(self.conv_b.teacher_last_read_message_id, 0)

    def test_teacher_thread_detail_renders_split_view_with_active_thread_and_cleared_unread(
        self,
    ):
        """
        Seam 3: Teacher accessing /messages/<conversation_id>/ receives 2-pane split-view
        on desktop with that conversation active in right pane and highlighted in left list.
        Read watermark is advanced for this conversation (clearing its unread count),
        while unselected conversations preserve unread counts.
        On mobile, back button returning to /messages/ is present.
        """
        self.client.force_login(self.teacher_user)
        url = reverse(
            "chat:conversation_detail",
            kwargs={"conversation_id": self.conv_b.id},
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chat/conversation_detail.html")

        # Split layout containers
        self.assertContains(response, "chat-split-layout")
        self.assertContains(response, "chat-split-sidebar")
        self.assertContains(response, "split_view_inbox.js")

        # Active conversation Bob is highlighted in the left list
        self.assertContains(response, f'data-conversation-id="{self.conv_b.id}"')
        self.assertContains(response, "active")

        # Right pane displays active timeline with messages
        self.assertContains(response, "Second msg from Bob")

        # Mobile back navigation button returning to /messages/
        expected_inbox_url = reverse("chat:inbox")
        self.assertContains(response, f'href="{expected_inbox_url}"')
        self.assertContains(response, "chat-mobile-back-btn")

        # Watermark advanced for Bob (to msg_b2)
        self.conv_b.refresh_from_db()
        self.assertEqual(self.conv_b.teacher_last_read_message_id, self.msg_b2.id)

        # Watermark NOT advanced for Alice (remains 0)
        self.conv_a.refresh_from_db()
        self.assertEqual(self.conv_a.teacher_last_read_message_id, 0)

        # In context conversations list, Bob's unread count is 0, Alice's is 1
        conv_list = response.context["conversations"]
        bob_item = next(c for c in conv_list if c["conversation"].id == self.conv_b.id)
        alice_item = next(
            c for c in conv_list if c["conversation"].id == self.conv_a.id
        )
        self.assertEqual(bob_item["unread_count"], 0)
        self.assertEqual(alice_item["unread_count"], 1)

    def test_teacher_views_empty_inbox_when_no_active_conversations(self):
        """Teacher with zero conversations sees the empty state."""
        new_teacher = User.objects.create_user(
            username="teacher_mark",
            email="mark@example.com",
            password="testpassword123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.client.force_login(new_teacher)
        response = self.client.get(reverse("chat:inbox"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No conversations yet")

    def test_teacher_accessing_uninitiated_conversation_includes_and_highlights_it_in_sidebar(
        self,
    ):
        """
        When a teacher accesses /messages/<conversation_id>/ for an uninitiated conversation
        (0 messages, last_message_at=None), it is included in the sidebar list and highlighted.
        """
        student_c = User.objects.create_user(
            username="student_charlie",
            email="charlie@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        conv_c = Conversation.objects.create(
            student=student_c,
            teacher=self.teacher_user,
        )
        self.assertIsNone(conv_c.last_message_at)

        self.client.force_login(self.teacher_user)
        url = reverse("chat:conversation_detail", kwargs={"conversation_id": conv_c.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-conversation-id="{conv_c.id}"')
        self.assertContains(response, "active")
        conv_ids = [c["conversation"].id for c in response.context["conversations"]]
        self.assertIn(conv_c.id, conv_ids)


class RoleAwareInboxStudentDetailTests(TestCase):
    """
    Seam 4: Student Direct Thread View & Access Control
    Verifies that a student accessing /messages/<conversation_id>/ receives the clean
    full-width single-pane timeline without split-view sidebar, advances the watermark,
    and outsider access is rejected with 404.
    """

    def setUp(self):
        cache.clear()
        self.client = Client()

        self.teacher_user = User.objects.create_user(
            username="teacher_jane",
            email="jane@example.com",
            password="testpassword123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.student = User.objects.create_user(
            username="student_alice",
            email="alice@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.outsider = User.objects.create_user(
            username="student_mallory",
            email="mallory@example.com",
            password="testpassword123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

        self.conv = Conversation.objects.create(
            student=self.student,
            teacher=self.teacher_user,
        )
        self.msg = send_message(self.conv, sender=self.teacher_user, text="Hi Alice")

    def tearDown(self):
        cache.clear()

    def test_student_conversation_detail_renders_single_pane_without_sidebar(self):
        """Student receives full-width single-pane view without split-view sidebar."""
        self.client.force_login(self.student)
        url = reverse(
            "chat:conversation_detail", kwargs={"conversation_id": self.conv.id}
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chat/conversation_detail.html")
        self.assertNotContains(response, '<aside class="chat-split-sidebar"')
        self.assertContains(response, "chat-single-pane")

        # Watermark is advanced
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.student_last_read_message_id, self.msg.id)

    def test_outsider_cannot_access_conversation_detail(self):
        """An outsider user receives 404 Not Found."""
        self.client.force_login(self.outsider)
        url = reverse(
            "chat:conversation_detail", kwargs={"conversation_id": self.conv.id}
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)
