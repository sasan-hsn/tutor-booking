from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.forms import StyledSetPasswordForm
from accounts.tokens import email_verification_token_generator

User = get_user_model()


class EmailVerificationViewsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            username='student_test',
            email='student_test@example.com',
            password='Password123!',
            role=User.Role.STUDENT,
        )
        self.teacher = User.objects.create_user(
            username='teacher_test',
            email='teacher_test@example.com',
            password='Password123!',
            role=User.Role.TEACHER,
        )

    def tearDown(self):
        cache.clear()

    def test_student_signup_dispatches_verification_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('accounts:student_signup'), {
                'username': 'fresh_student',
                'email': 'fresh_student@example.com',
                'password1': 'ComplexPass123',
                'password2': 'ComplexPass123',
            })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='fresh_student')
        self.assertFalse(user.is_email_verified)
        self.assertEqual(len(mail.outbox), 1)

        email = mail.outbox[0]
        self.assertEqual(email.to, ['fresh_student@example.com'])
        self.assertIn('Verify your email address', email.subject)
        self.assertIn('/accounts/verify-email/', email.body)

    def test_teacher_signup_dispatches_verification_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('accounts:teacher_signup'), {
                'username': 'fresh_teacher',
                'email': 'fresh_teacher@example.com',
                'password1': 'ComplexPass123',
                'password2': 'ComplexPass123',
                'timezone': 'UTC',
            })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='fresh_teacher')
        self.assertFalse(user.is_email_verified)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['fresh_teacher@example.com'])

    def test_student_signup_preserves_next_url_in_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('accounts:student_signup') + '?next=/',
                {
                    'username': 'next_student',
                    'email': 'next_student@example.com',
                    'password1': 'ComplexPass123',
                    'password2': 'ComplexPass123',
                    'next': '/',
                }
            )
        self.assertRedirects(response, '/')
        email = mail.outbox[0]
        self.assertIn('next=/', email.body)

    def test_verify_email_logged_out_auto_logins_and_verifies(self):
        token = email_verification_token_generator.make_token(self.student)
        url = reverse('accounts:verify_email', kwargs={'token': token})

        response = self.client.get(url)
        self.assertRedirects(response, reverse('booking:student_dashboard'))

        self.student.refresh_from_db()
        self.assertTrue(self.student.is_email_verified)

        # Check user is logged in
        self.assertEqual(int(self.client.session['_auth_user_id']), self.student.pk)

    def test_verify_email_logged_out_with_safe_next_redirect(self):
        token = email_verification_token_generator.make_token(self.student)
        url = reverse('accounts:verify_email', kwargs={'token': token}) + '?next=/'

        response = self.client.get(url)
        self.assertRedirects(response, '/')
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_email_verified)

    def test_verify_email_logged_out_with_unsafe_next_redirect_ignored(self):
        token = email_verification_token_generator.make_token(self.student)
        url = reverse('accounts:verify_email', kwargs={'token': token}) + '?next=https://malicious.com'

        response = self.client.get(url)
        self.assertRedirects(response, reverse('booking:student_dashboard'))
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_email_verified)

    def test_verify_email_logged_in_as_same_user(self):
        self.client.force_login(self.student)
        token = email_verification_token_generator.make_token(self.student)
        url = reverse('accounts:verify_email', kwargs={'token': token})

        response = self.client.get(url)
        self.assertRedirects(response, reverse('booking:student_dashboard'))
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_email_verified)

    def test_verify_email_cross_session_conflict_renders_interception_screen(self):
        # Teacher is logged in, but clicks link for Student
        self.client.force_login(self.teacher)
        token = email_verification_token_generator.make_token(self.student)
        url = reverse('accounts:verify_email', kwargs={'token': token})

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/verify_email_conflict.html')
        self.assertContains(response, 'Account Session Conflict')
        self.assertContains(response, self.teacher.username)
        self.assertContains(response, self.student.email)

        # Neither session nor student state should be mutated
        self.assertEqual(int(self.client.session['_auth_user_id']), self.teacher.pk)
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_email_verified)

    def test_verify_email_expired_token_renders_error_with_resend_form(self):
        token = email_verification_token_generator.make_token(self.student)
        url = reverse('accounts:verify_email', kwargs={'token': token})

        with patch.object(email_verification_token_generator, 'TOKEN_TTL', -1):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, 'accounts/verify_email_invalid.html')
        self.assertContains(response, 'Verification Link Expired', status_code=400)
        self.assertContains(response, self.student.email, status_code=400)

        self.student.refresh_from_db()
        self.assertFalse(self.student.is_email_verified)

    def test_verify_email_tampered_token_renders_invalid_error(self):
        token = email_verification_token_generator.make_token(self.student)
        bad_token = token + "corrupted"
        url = reverse('accounts:verify_email', kwargs={'token': bad_token})

        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, 'accounts/verify_email_invalid.html')
        self.assertContains(response, 'Invalid Verification Link', status_code=400)

        self.student.refresh_from_db()
        self.assertFalse(self.student.is_email_verified)

    def test_verify_email_already_verified_redirects_gracefully(self):
        self.student.is_email_verified = True
        self.student.save()

        token = email_verification_token_generator.make_token(self.student)
        url = reverse('accounts:verify_email', kwargs={'token': token})

        response = self.client.get(url)
        self.assertRedirects(response, reverse('booking:student_dashboard'))

    def test_resend_verification_requires_post(self):
        response = self.client.get(reverse('accounts:resend_verification_email'))
        self.assertEqual(response.status_code, 405)

    def test_resend_verification_authenticated_user_success(self):
        self.client.force_login(self.student)
        response = self.client.post(reverse('accounts:resend_verification_email'))
        self.assertEqual(response.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.student.email])

    def test_resend_verification_cooldown_throttle(self):
        self.client.force_login(self.student)
        # First request succeeds
        r1 = self.client.post(reverse('accounts:resend_verification_email'))
        self.assertEqual(len(mail.outbox), 1)

        # Immediate second request should be throttled
        r2 = self.client.post(
            reverse('accounts:resend_verification_email'),
            headers={'x-requested-with': 'XMLHttpRequest'},
        )
        self.assertEqual(r2.status_code, 429)
        data = r2.json()
        self.assertEqual(data.get('reason'), 'cooldown')
        self.assertGreater(data.get('retry_after'), 0)
        # Outbox should still only have 1 email
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_verification_hourly_ceiling_throttle(self):
        from accounts.rate_limiting import record_resend_attempt
        # Record 5 attempts
        for _ in range(5):
            record_resend_attempt(user=self.student, cooldown_seconds=0)

        self.client.force_login(self.student)
        response = self.client.post(
            reverse('accounts:resend_verification_email'),
            headers={'x-requested-with': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 429)
        data = response.json()
        self.assertEqual(data.get('reason'), 'ceiling')
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_verification_already_verified_user_no_email(self):
        self.student.is_email_verified = True
        self.student.save()
        self.client.force_login(self.student)

        response = self.client.post(
            reverse('accounts:resend_verification_email'),
            headers={'x-requested-with': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get('already_verified'))
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_verification_unauthenticated_with_email_success(self):
        response = self.client.post(reverse('accounts:resend_verification_email'), {
            'email': self.student.email,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.student.email])

    def test_password_reset_completion_auto_verifies_email(self):
        self.assertFalse(self.student.is_email_verified)
        form = StyledSetPasswordForm(user=self.student, data={
            'new_password1': 'BrandNewPass123!',
            'new_password2': 'BrandNewPass123!',
        })
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.student.refresh_from_db()
        self.assertTrue(self.student.is_email_verified)

    def test_logout_view_respects_safe_next_url(self):
        self.client.force_login(self.student)
        token = email_verification_token_generator.make_token(self.teacher)
        next_destination = reverse('accounts:verify_email', kwargs={'token': token})
        response = self.client.post(reverse('accounts:logout'), {
            'next': next_destination,
        })
        self.assertRedirects(response, next_destination, target_status_code=302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.teacher.pk)

    def test_logout_view_ignores_unsafe_next_url(self):
        self.client.force_login(self.student)
        response = self.client.post(reverse('accounts:logout'), {
            'next': 'https://malicious.com',
        })
        self.assertRedirects(response, '/')
        self.assertNotIn('_auth_user_id', self.client.session)
