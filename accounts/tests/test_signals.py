from accounts.models import User, StudentProfile
from portfolio.models import TeacherProfile
from django.test import TestCase


class TeacherProfileSignalTestCase(TestCase):
    def test_teacher_profile_created_on_user_creation(self):
        user = User.objects.create_user(username='testteacher2', password='testpassword', role=User.Role.TEACHER)
        self.assertTrue(TeacherProfile.objects.filter(user=user).exists())
        self.assertEqual(user.teacher_profile.user, user)

    def test_teacher_profile_not_created_for_student(self):
        user = User.objects.create_user(username='teststudent2', password='testpassword', role=User.Role.STUDENT)
        self.assertFalse(TeacherProfile.objects.filter(user=user).exists())



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