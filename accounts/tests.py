from django.test import TestCase
from accounts.models import User, StudentProfile

class StudentProfileSignalTestCase(TestCase):
    def test_student_profile_created_on_user_creation(self):
        # Create a new user with the role of STUDENT
        user = User.objects.create_user(username='teststudent', password='testpassword', role=User.Role.STUDENT)

        # Check if the StudentProfile was created
        self.assertTrue(StudentProfile.objects.filter(user=user).exists())
        self.assertEqual(user.student_profile.user, user)

    def test_student_profile_not_created_for_teacher(self):
        # Create a new user with the role of TEACHER
        user = User.objects.create_user(username='testteacher', password='testpassword', role=User.Role.TEACHER)

        # Check if the StudentProfile was not created
        self.assertFalse(StudentProfile.objects.filter(user=user).exists())    