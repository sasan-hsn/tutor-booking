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
            username='access_teacher', password='password123', role=User.Role.TEACHER
        )
        cls.student_user = User.objects.create_user(
            username='access_student', password='password123', role=User.Role.STUDENT
        )

    def setUp(self):
        self.teacher_client = self.client_class()
        self.teacher_client.force_login(self.teacher_user)

        self.student_client = self.client_class()
        self.student_client.force_login(self.student_user)

        self.anon_client = self.client_class()