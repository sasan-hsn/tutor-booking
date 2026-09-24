from django.test import TestCase
from django.contrib.auth import get_user_model

User = get_user_model()


class RoleTestCase(TestCase):
    """Base test case providing a logged-in student client, a
    logged-in teacher client, and an anonymous client — reusable
    across apps for access-control and view tests."""

    @classmethod
    def setUpTestData(cls):
        cls.teacher_user = User.objects.create_user(
            username='access_teacher',
            email='access_teacher@example.com',
            password='password123',
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        cls.student_user = User.objects.create_user(
            username='access_student',
            email='access_student@example.com',
            password='password123',
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

    def setUp(self):
        self.teacher_client = self.client_class()
        self.teacher_client.force_login(self.teacher_user)

        self.student_client = self.client_class()
        self.student_client.force_login(self.student_user)

        self.anon_client = self.client_class()