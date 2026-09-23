from django.test import TestCase
from accounts.forms import StudentSignUpForm, TeacherSignUpForm
from accounts.models import User


class SignUpFormEmailTests(TestCase):
    password = 'TestingPass123!@#'

    def test_student_signup_form_normalizes_email(self):
        form_data = {
            'username': 'newstudent',
            'email': 'Student@Example.COM',
            'password1': self.password,
            'password2': self.password,
        }
        form = StudentSignUpForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['email'], 'student@example.com')
        user = form.save()
        self.assertEqual(user.email, 'student@example.com')

    def test_student_signup_form_rejects_duplicate_email_case_insensitively(self):
        User.objects.create_user(
            username='existinguser',
            password=self.password,
            email='existing@example.com',
        )
        form_data = {
            'username': 'newstudent',
            'email': 'EXISTING@EXAMPLE.COM',
            'password1': self.password,
            'password2': self.password,
        }
        form = StudentSignUpForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
        self.assertEqual(
            form.errors['email'],
            ['A user with that email already exists.'],
        )

    def test_teacher_signup_form_normalizes_email(self):
        form_data = {
            'username': 'newteacher',
            'email': 'Teacher@Example.COM',
            'password1': self.password,
            'password2': self.password,
        }
        form = TeacherSignUpForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['email'], 'teacher@example.com')
        user = form.save()
        self.assertEqual(user.email, 'teacher@example.com')

    def test_teacher_signup_form_rejects_duplicate_email_case_insensitively(self):
        User.objects.create_user(
            username='existinguser',
            password=self.password,
            email='teacher@example.com',
        )
        form_data = {
            'username': 'newteacher',
            'email': 'TEACHER@EXAMPLE.COM',
            'password1': self.password,
            'password2': self.password,
        }
        form = TeacherSignUpForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
        self.assertEqual(
            form.errors['email'],
            ['A user with that email already exists.'],
        )

    def test_signup_form_allows_same_email_for_existing_instance(self):
        user = User.objects.create_user(
            username='selfuser',
            password=self.password,
            email='self@example.com',
        )
        form_data = {
            'username': 'selfuser',
            'email': 'SELF@EXAMPLE.COM',
            'password1': self.password,
            'password2': self.password,
        }
        form = StudentSignUpForm(data=form_data, instance=user)
        form.full_clean()
        self.assertNotIn('email', form.errors)
