from django.test import TestCase
from accounts.forms import StudentProfileSettingsForm, StudentSignUpForm, TeacherSignUpForm
from accounts.models import User
from portfolio.forms import TeacherAccountSettingsForm


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

    def test_signup_form_rejects_email_claimed_as_pending_email_by_another_user(self):
        User.objects.create_user(
            username='pendinguser',
            password=self.password,
            email='active@example.com',
            pending_email='pending_claimed@example.com',
        )
        form_data = {
            'username': 'newstudent',
            'email': 'PENDING_CLAIMED@EXAMPLE.COM',
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


class ProfileSettingsFormEmailTests(TestCase):
    password = 'TestingPass123!@#'

    def setUp(self):
        self.user = User.objects.create_user(
            username='student1',
            email='student1@example.com',
            password=self.password,
            role=User.Role.STUDENT,
        )
        self.other_user = User.objects.create_user(
            username='student2',
            email='student2@example.com',
            pending_email='pending_other@example.com',
            password=self.password,
            role=User.Role.STUDENT,
        )
        self.teacher_user = User.objects.create_user(
            username='teacher1',
            email='teacher1@example.com',
            password=self.password,
            role=User.Role.TEACHER,
        )

    def test_student_profile_settings_form_initial_email(self):
        form = StudentProfileSettingsForm(user=self.user)
        self.assertEqual(form.fields['email'].initial, 'student1@example.com')

    def test_student_profile_settings_form_rejects_duplicate_active_email(self):
        form_data = {
            'email': 'STUDENT2@EXAMPLE.COM',
            'timezone': 'UTC',
        }
        form = StudentProfileSettingsForm(data=form_data, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
        self.assertEqual(
            form.errors['email'],
            ['A user with that email already exists.'],
        )

    def test_student_profile_settings_form_rejects_duplicate_pending_email(self):
        form_data = {
            'email': 'PENDING_OTHER@EXAMPLE.COM',
            'timezone': 'UTC',
        }
        form = StudentProfileSettingsForm(data=form_data, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
        self.assertEqual(
            form.errors['email'],
            ['A user with that email already exists.'],
        )

    def test_student_profile_settings_form_allows_own_current_email(self):
        form_data = {
            'email': 'STUDENT1@EXAMPLE.COM',
            'timezone': 'UTC',
        }
        form = StudentProfileSettingsForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        email_changed = form.save()
        self.assertFalse(email_changed)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'student1@example.com')
        self.assertIsNone(self.user.pending_email)

    def test_student_profile_settings_form_sets_pending_email_without_changing_active_email(self):
        form_data = {
            'email': 'brandnew@example.com',
            'timezone': 'UTC',
        }
        form = StudentProfileSettingsForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        email_changed = form.save()
        self.assertTrue(email_changed)
        self.user.refresh_from_db()
        # Active email remains unchanged!
        self.assertEqual(self.user.email, 'student1@example.com')
        # Pending email holds the new address
        self.assertEqual(self.user.pending_email, 'brandnew@example.com')

    def test_teacher_account_settings_form_initial_email(self):
        form = TeacherAccountSettingsForm(user=self.teacher_user)
        self.assertEqual(form.fields['email'].initial, 'teacher1@example.com')

    def test_teacher_account_settings_form_rejects_duplicate_active_and_pending_email(self):
        # Test active email collision
        form_data = {
            'username': 'teacher1',
            'email': 'STUDENT2@EXAMPLE.COM',
            'first_name': 'Teacher',
            'last_name': 'One',
            'timezone': 'UTC',
        }
        form = TeacherAccountSettingsForm(data=form_data, user=self.teacher_user)
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

        # Test pending email collision
        form_data['email'] = 'PENDING_OTHER@EXAMPLE.COM'
        form = TeacherAccountSettingsForm(data=form_data, user=self.teacher_user)
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_teacher_account_settings_form_sets_pending_email_without_changing_active_email(self):
        form_data = {
            'username': 'teacher1',
            'email': 'newteacher@example.com',
            'first_name': 'Teacher',
            'last_name': 'One',
            'timezone': 'UTC',
        }
        form = TeacherAccountSettingsForm(data=form_data, user=self.teacher_user)
        self.assertTrue(form.is_valid(), form.errors)
        email_changed = form.save()
        self.assertTrue(email_changed)
        self.teacher_user.refresh_from_db()
        self.assertEqual(self.teacher_user.email, 'teacher1@example.com')
        self.assertEqual(self.teacher_user.pending_email, 'newteacher@example.com')
