from django.contrib.auth import get_user_model
from django.test import RequestFactory

from accounts.tests.base import RoleTestCase
from chat.context_processors import chat_context
from chat.models import Conversation, Message
from chat.services import get_or_create_conversation, send_message

User = get_user_model()


class ChatContextProcessorTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.other_student = User.objects.create_user(
            username="other_student",
            email="other_student@example.com",
            password="password123",
            role="student",
            is_email_verified=True,
        )

    def test_anonymous_user_returns_zero_unread(self):
        from django.contrib.auth.models import AnonymousUser

        request = self.factory.get("/")
        request.user = AnonymousUser()

        context = chat_context(request)
        self.assertEqual(context["unread_chat_count"], 0)

    def test_authenticated_user_with_no_messages_returns_zero(self):
        request = self.factory.get("/dashboard/student/")
        request.user = self.student_user

        context = chat_context(request)
        self.assertEqual(context["unread_chat_count"], 0)

    def test_authenticated_user_counts_unread_across_multiple_conversations(self):
        # Teacher has conversations with two students
        conv1, _ = get_or_create_conversation(self.student_user, self.teacher_user)
        conv2, _ = get_or_create_conversation(self.other_student, self.teacher_user)

        # Student 1 sends 2 messages to teacher
        send_message(conv1, sender=self.student_user, text="Msg 1 from S1")
        send_message(conv1, sender=self.student_user, text="Msg 2 from S1")

        # Student 2 sends 3 messages to teacher
        send_message(conv2, sender=self.other_student, text="Msg 1 from S2")
        send_message(conv2, sender=self.other_student, text="Msg 2 from S2")
        send_message(conv2, sender=self.other_student, text="Msg 3 from S2")

        # Teacher should see total 5 unread messages
        request = self.factory.get("/dashboard/teacher/")
        request.user = self.teacher_user
        context = chat_context(request)
        self.assertEqual(context["unread_chat_count"], 5)

        # Student 1 should see 0 unread messages (they sent them)
        request.user = self.student_user
        context = chat_context(request)
        self.assertEqual(context["unread_chat_count"], 0)

        # Teacher replies to Student 1
        send_message(conv1, sender=self.teacher_user, text="Reply to S1")

        # Now Student 1 should see 1 unread message
        context = chat_context(request)
        self.assertEqual(context["unread_chat_count"], 1)
