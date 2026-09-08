from django.test import TestCase
from django.urls import reverse
from accounts.models import User
from portfolio.models import TeacherProfile

class SignupViewTestCase(TestCase):
    def test_signup_creates_authenticated_student(self):
        response = self.client.post(reverse('accounts:student_signup'), {
            'username': 'newstudent',
            'email': 'newstudent@example.com',
            'password1': 'ComplexPass123',
            'password2': 'ComplexPass123',
        })
        self.assertEqual(response.wsgi_request.user.role, User.Role.STUDENT)
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_signup_ignores_injected_role_field(self):
        self.client.post(reverse('accounts:student_signup'), {
            'username': 'sneakyuser',
            'email': 'sneaky@example.com',
            'password1': 'ComplexPass123',
            'password2': 'ComplexPass123',
            'role': 'teacher',
        })
        user = User.objects.get(username='sneakyuser')
        self.assertEqual(user.role, User.Role.STUDENT)    



class LoginViewTestCase(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username='student1', password='testpass123', role=User.Role.STUDENT
        )
        self.teacher = User.objects.create_user(
            username='teacher1', password='testpass123', role=User.Role.TEACHER
        )

    def test_student_login_authenticates_and_redirects(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'testpass123',
        })
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertEqual(response.wsgi_request.user.role, User.Role.STUDENT)
        self.assertRedirects(response, reverse('booking:student_dashboard'))

    def test_teacher_login_authenticates_and_redirects(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'teacher1',
            'password': 'testpass123',
        })
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertEqual(response.wsgi_request.user.role, User.Role.TEACHER)
        self.assertRedirects(response, reverse('booking:teacher_dashboard'))

    def test_invalid_credentials_not_authenticated(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'wrongpassword',
        })
        self.assertFalse(response.wsgi_request.user.is_authenticated)   


class LogoutViewTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='logouttest', password='testpass123', role=User.Role.STUDENT
        )

    def test_logout_clears_session(self):
        self.client.login(username='logouttest', password='testpass123')
        response = self.client.post(reverse('accounts:logout'))
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_logout_via_get_does_not_log_out(self):
        self.client.login(username='logouttest', password='testpass123')
        response = self.client.get(reverse('accounts:logout'))
        self.assertEqual(response.status_code, 405)

        self.assertIn('_auth_user_id', self.client.session)


class TeacherSignUpViewTestCase(TestCase):
    def test_signup_creates_authenticated_teacher_with_exactly_one_profile(self):
        response = self.client.post(reverse('accounts:teacher_signup'), {
            'username': 'new_teacher',
            'email': 'newteacher@example.com',
            'password1': 'StrongPass123',
            'password2': 'StrongPass123',
            'timezone': 'UTC',
        })

        self.assertEqual(response.status_code, 302)

        user = User.objects.get(username='new_teacher')
        self.assertEqual(user.role, User.Role.TEACHER)

        # regression test: a signal + a form-level create() once raced
        # and caused IntegrityError — confirm exactly one profile exists,
        # not zero and not two
        self.assertEqual(TeacherProfile.objects.filter(user=user).count(), 1)

        # confirm the user is actually logged in after signup
        response = self.client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_signup_ignores_injected_role_field(self):
        # mirrors the equivalent student test — even if someone tries
        # to POST role=student through this teacher-signup form, the
        # view must force role=TEACHER regardless of form input
        response = self.client.post(reverse('accounts:teacher_signup'), {
            'username': 'sneaky_teacher',
            'email': 'sneaky@example.com',
            'password1': 'StrongPass123',
            'password2': 'StrongPass123',
            'timezone': 'UTC',
            'role': 'student',
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='sneaky_teacher')
        self.assertEqual(user.role, User.Role.TEACHER)        