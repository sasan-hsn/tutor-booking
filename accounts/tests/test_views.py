from unittest.mock import patch
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import User
from accounts.tokens import (
    email_change_revocation_token_generator,
    email_change_token_generator,
    email_verification_token_generator,
)
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
        cache.clear()
        self.student = User.objects.create_user(
            username='student1', password='testpass123', role=User.Role.STUDENT
        )
        self.teacher = User.objects.create_user(
            username='teacher1', password='testpass123', role=User.Role.TEACHER
        )

    def tearDown(self):
        super().tearDown()
        cache.clear()

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

    def test_login_page_renders_clean_without_error_alert(self):
        response = self.client.get(reverse('accounts:login'))
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertFalse(form.non_field_errors())
        self.assertNotContains(response, 'alert alert-danger')

    def test_invalid_credentials_not_authenticated(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'wrongpassword',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        form = response.context['form']
        self.assertTrue(form.non_field_errors())
        self.assertContains(response, 'alert alert-danger')
        self.assertContains(response, 'Please enter a correct')

    def test_login_rate_limiting_locks_out_on_sixth_attempt(self):
        for i in range(5):
            response = self.client.post(reverse('accounts:login'), {
                'username': 'student1',
                'password': 'wrongpassword',
            })
            self.assertEqual(response.status_code, 200, f"Attempt {i+1} should return 200")

        # 6th attempt should be rejected with 429 Too Many Requests
        response = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'wrongpassword',
        })
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response.headers)
        retry_after = int(response['Retry-After'])
        self.assertGreater(retry_after, 0)
        self.assertLessEqual(retry_after, 300)
        self.assertContains(response, 'alert alert-danger', status_code=429)
        self.assertContains(response, 'Too many failed login attempts', status_code=429)
        self.assertContains(response, 'minute', status_code=429)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_pre_auth_short_circuit_prevents_password_hashing(self):
        for _ in range(5):
            self.client.post(reverse('accounts:login'), {
                'username': 'student1',
                'password': 'wrongpassword',
            })

        # During lockout, authenticate() should never be invoked
        with patch('django.contrib.auth.authenticate') as mock_auth:
            response = self.client.post(reverse('accounts:login'), {
                'username': 'student1',
                'password': 'testpass123',
            })
            self.assertEqual(response.status_code, 429)
            mock_auth.assert_not_called()

    def test_passive_rejection_does_not_extend_lockout(self):
        with patch('time.time', return_value=1000.0):
            for _ in range(5):
                self.client.post(reverse('accounts:login'), {
                    'username': 'student1',
                    'password': 'wrongpassword',
                })

        with patch('time.time', return_value=1050.0):
            response = self.client.post(reverse('accounts:login'), {
                'username': 'student1',
                'password': 'wrongpassword',
            })
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response['Retry-After'], '250')

        # Checking at t=1100 should show remaining 200s, proving cooldown was not reset to 300s
        with patch('time.time', return_value=1100.0):
            response = self.client.post(reverse('accounts:login'), {
                'username': 'student1',
                'password': 'wrongpassword',
            })
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response['Retry-After'], '200')

    def test_successful_login_resets_rate_limit(self):
        for _ in range(4):
            self.client.post(reverse('accounts:login'), {
                'username': 'student1',
                'password': 'wrongpassword',
            })

        # 5th attempt is valid -> authenticates and clears counter
        response = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'testpass123',
        })
        self.assertRedirects(response, reverse('booking:student_dashboard'))

        # Subsequent failed attempt is allowed as attempt 1 (200, not 429)
        response = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'wrongpassword',
        })
        self.assertEqual(response.status_code, 200)

    def test_ip_isolation_allows_login_from_different_ip(self):
        # 5 failed attempts from IP A
        for _ in range(5):
            self.client.post(reverse('accounts:login'), {
                'username': 'student1',
                'password': 'wrongpassword',
            }, REMOTE_ADDR='198.51.100.1')

        # IP A is locked out
        response_ip_a = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'testpass123',
        }, REMOTE_ADDR='198.51.100.1')
        self.assertEqual(response_ip_a.status_code, 429)

        # IP B can log in successfully with valid credentials
        response_ip_b = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'testpass123',
        }, REMOTE_ADDR='198.51.100.2')
        self.assertRedirects(response_ip_b, reverse('booking:student_dashboard'))

    def test_username_normalization_applies_across_case_and_whitespace(self):
        for _ in range(2):
            self.client.post(reverse('accounts:login'), {
                'username': '  student1  ',
                'password': 'wrongpassword',
            })
        for _ in range(3):
            self.client.post(reverse('accounts:login'), {
                'username': 'STUDENT1',
                'password': 'wrongpassword',
            })

        # 6th attempt with lowercase normalized username is locked out
        response = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'wrongpassword',
        })
        self.assertEqual(response.status_code, 429)

    def test_login_fails_open_when_cache_backend_errors(self):
        # Simulate Redis ResponseError: unknown command `HELLO` or connectivity crash
        with patch.object(cache, 'get', side_effect=Exception("unknown command `HELLO`")):
            # Valid login should succeed without 500
            response = self.client.post(reverse('accounts:login'), {
                'username': 'student1',
                'password': 'testpass123',
            })
            self.assertRedirects(response, reverse('booking:student_dashboard'))
            self.assertTrue(response.wsgi_request.user.is_authenticated)

        with patch.object(cache, 'get', side_effect=Exception("unknown command `HELLO`")), \
             patch.object(cache, 'set', side_effect=Exception("unknown command `HELLO`")):
            # Invalid login should return 200 with invalid credentials message, not 500
            response = self.client.post(reverse('accounts:login'), {
                'username': 'student1',
                'password': 'wrongpassword',
            })
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Please enter a correct')

    def test_global_ip_rate_limiting_locks_out_on_twenty_first_attempt_across_multiple_users(self):
        client_ip = '198.51.100.99'
        for i in range(20):
            response = self.client.post(reverse('accounts:login'), {
                'username': f'sprayed_user_{i}',
                'password': 'wrongpassword',
            }, REMOTE_ADDR=client_ip)
            self.assertEqual(response.status_code, 200, f"Attempt {i+1} should return 200")

        # 21st attempt across different username from the same IP is locked out
        response = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'wrongpassword',
        }, REMOTE_ADDR=client_ip)
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response.headers)
        retry_after = int(response['Retry-After'])
        self.assertGreater(retry_after, 0)
        self.assertLessEqual(retry_after, 900)
        self.assertContains(response, 'alert alert-danger', status_code=429)
        self.assertContains(response, 'Too many failed login attempts', status_code=429)
        self.assertContains(response, '15 minutes', status_code=429)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_global_ip_lockout_pre_auth_short_circuit_prevents_password_hashing(self):
        client_ip = '198.51.100.99'
        for i in range(20):
            self.client.post(reverse('accounts:login'), {
                'username': f'sprayed_user_{i}',
                'password': 'wrongpassword',
            }, REMOTE_ADDR=client_ip)

        with patch('django.contrib.auth.authenticate') as mock_auth:
            response = self.client.post(reverse('accounts:login'), {
                'username': 'student1',
                'password': 'testpass123',
            }, REMOTE_ADDR=client_ip)
            self.assertEqual(response.status_code, 429)
            mock_auth.assert_not_called()

    def test_global_ip_lockout_structured_security_logging(self):
        client_ip = '198.51.100.99'
        for i in range(20):
            self.client.post(reverse('accounts:login'), {
                'username': f'sprayed_user_{i}',
                'password': 'wrongpassword',
            }, REMOTE_ADDR=client_ip)

        with self.assertLogs('accounts.rate_limiting', level='WARNING') as cm:
            response = self.client.post(reverse('accounts:login'), {
                'username': 'student1',
                'password': 'SuperSecretPasswordNeverLog123!',
            }, REMOTE_ADDR=client_ip)
            self.assertEqual(response.status_code, 429)

        self.assertIn(client_ip, cm.output[0])
        self.assertIn('student1', cm.output[0])
        self.assertIn('ip_ceiling', cm.output[0])
        self.assertNotIn('SuperSecretPasswordNeverLog123!', cm.output[0])

    def test_global_ip_lockout_allows_different_ip(self):
        ip_a = '198.51.100.99'
        ip_b = '198.51.100.100'

        for i in range(20):
            self.client.post(reverse('accounts:login'), {
                'username': f'sprayed_user_{i}',
                'password': 'wrongpassword',
            }, REMOTE_ADDR=ip_a)

        # IP A is locked out
        response_ip_a = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'testpass123',
        }, REMOTE_ADDR=ip_a)
        self.assertEqual(response_ip_a.status_code, 429)

        # IP B can log in successfully with valid credentials
        response_ip_b = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'testpass123',
        }, REMOTE_ADDR=ip_b)
        self.assertRedirects(response_ip_b, reverse('booking:student_dashboard'))

    def test_successful_login_does_not_reset_global_ip_ceiling(self):
        client_ip = '198.51.100.99'
        # 19 failed attempts across sprayed usernames
        for i in range(19):
            self.client.post(reverse('accounts:login'), {
                'username': f'sprayed_user_{i}',
                'password': 'wrongpassword',
            }, REMOTE_ADDR=client_ip)

        # 20th attempt: legitimate user logs in successfully
        valid_response = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'testpass123',
        }, REMOTE_ADDR=client_ip)
        self.assertRedirects(valid_response, reverse('booking:student_dashboard'))

        # Log out
        self.client.logout()

        # Another failed attempt from the same IP (makes total IP failures = 20)
        fail_response = self.client.post(reverse('accounts:login'), {
            'username': 'student1',
            'password': 'wrongpassword',
        }, REMOTE_ADDR=client_ip)
        self.assertEqual(fail_response.status_code, 200)

        # 21st attempt is now locked out because IP ceiling was NOT cleared by successful login
        blocked_response = self.client.post(reverse('accounts:login'), {
            'username': 'other_student',
            'password': 'wrongpassword',
        }, REMOTE_ADDR=client_ip)
        self.assertEqual(blocked_response.status_code, 429)

   


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


class PasswordResetTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='resetuser',
            email='resetuser@example.com',
            password='OldPassword123!',
            role=User.Role.STUDENT,
        )
        self.inactive_user = User.objects.create_user(
            username='inactiveuser',
            email='inactive@example.com',
            password='OldPassword123!',
            is_active=False,
            role=User.Role.STUDENT,
        )

    def test_login_page_has_forgot_password_link(self):
        response = self.client.get(reverse('accounts:login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('accounts:password_reset'))
        self.assertContains(response, 'Forgot password?')

    def test_password_reset_request_valid_email_sends_email_and_redirects(self):
        response = self.client.post(reverse('accounts:password_reset'), {
            'email': 'resetuser@example.com',
        })
        self.assertRedirects(response, reverse('accounts:password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['resetuser@example.com'])
        self.assertIn('Password reset for English with Mary', email.subject)
        self.assertIn('24 hours', email.body)
        # Verify HTML alternative was also generated with the brand and link
        self.assertEqual(len(email.alternatives), 1)
        self.assertIn('24 hours', email.alternatives[0][0])
        # Check reset URL structure in the email body
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        self.assertIn(f'/accounts/reset/{uid}/', email.body)

    def test_password_reset_request_nonexistent_email_sends_no_email_and_redirects(self):
        response = self.client.post(reverse('accounts:password_reset'), {
            'email': 'nonexistent@example.com',
        })
        self.assertRedirects(response, reverse('accounts:password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)

    def test_password_reset_request_inactive_user_sends_no_email(self):
        response = self.client.post(reverse('accounts:password_reset'), {
            'email': 'inactive@example.com',
        })
        self.assertRedirects(response, reverse('accounts:password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)

    def _confirm_password_reset(self, user, new_password='NewValidPassword123!'):
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_url = reverse('accounts:password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
        response = self.client.get(reset_url, follow=True)
        self.assertEqual(response.status_code, 200)
        post_url = response.redirect_chain[0][0]
        return self.client.post(post_url, {
            'new_password1': new_password,
            'new_password2': new_password,
        })

    def test_password_reset_confirm_valid_token_changes_password(self):
        self.assertFalse(self.user.is_email_verified)
        post_response = self._confirm_password_reset(self.user, 'NewValidPassword123!')
        self.assertRedirects(post_response, reverse('accounts:password_reset_complete'))

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_email_verified)
        self.assertFalse(self.user.check_password('OldPassword123!'))
        self.assertTrue(self.user.check_password('NewValidPassword123!'))

        login_response = self.client.post(reverse('accounts:login'), {
            'username': 'resetuser',
            'password': 'NewValidPassword123!',
        })
        self.assertTrue(login_response.wsgi_request.user.is_authenticated)

    def test_password_reset_confirm_auto_verifies_unverified_user_and_invalidates_signup_token(self):
        self.assertFalse(self.user.is_email_verified)
        signup_token = email_verification_token_generator.make_token(self.user)
        user, status = email_verification_token_generator.check_token(signup_token)
        self.assertEqual(status, 'valid')
        self.assertEqual(user, self.user)

        post_response = self._confirm_password_reset(self.user, 'BrandNewPass123!')
        self.assertRedirects(post_response, reverse('accounts:password_reset_complete'))

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_email_verified)
        self.assertTrue(self.user.check_password('BrandNewPass123!'))

        invalid_user, invalid_status = email_verification_token_generator.check_token(signup_token)
        self.assertEqual(invalid_status, 'invalid')
        self.assertIsNone(invalid_user)

        verify_url = reverse('accounts:verify_email', kwargs={'token': signup_token})
        verify_response = self.client.get(verify_url)
        self.assertEqual(verify_response.status_code, 400)
        self.assertTemplateUsed(verify_response, 'accounts/verify_email_invalid.html')

    def test_password_reset_confirm_invalidates_outstanding_email_change_and_revocation_tokens(self):
        self.user.pending_email = 'pending_new@example.com'
        self.user.save()

        change_token = email_change_token_generator.make_token(self.user)
        revocation_token = email_change_revocation_token_generator.make_token(self.user)

        user, status = email_change_token_generator.check_token(change_token)
        self.assertEqual(status, 'valid')
        user, status = email_change_revocation_token_generator.check_token(revocation_token)
        self.assertEqual(status, 'valid')

        self._confirm_password_reset(self.user, 'BrandNewPass123!')

        user, status = email_change_token_generator.check_token(change_token)
        self.assertEqual(status, 'invalid')
        self.assertIsNone(user)

        user, status = email_change_revocation_token_generator.check_token(revocation_token)
        self.assertEqual(status, 'invalid')
        self.assertIsNone(user)

        confirm_url = reverse('accounts:confirm_email_change', kwargs={'token': change_token})
        confirm_response = self.client.get(confirm_url)
        self.assertEqual(confirm_response.status_code, 400)
        self.assertTemplateUsed(confirm_response, 'accounts/verify_email_invalid.html')

        revoke_url = reverse('accounts:revoke_email_change', kwargs={'token': revocation_token})
        revoke_response = self.client.get(revoke_url)
        self.assertEqual(revoke_response.status_code, 400)
        self.assertTemplateUsed(revoke_response, 'accounts/revoke_email_change_invalid.html')

    def test_password_reset_confirm_preserves_verified_status_for_already_verified_user(self):
        self.user.is_email_verified = True
        self.user.save()

        self._confirm_password_reset(self.user, 'BrandNewPass123!')

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_email_verified)
        self.assertTrue(self.user.check_password('BrandNewPass123!'))

    def test_teacher_password_reset_confirm_auto_verifies_email(self):
        teacher = User.objects.create_user(
            username='resetteacher',
            email='resetteacher@example.com',
            password='OldPassword123!',
            role=User.Role.TEACHER,
        )
        self.assertFalse(teacher.is_email_verified)

        self._confirm_password_reset(teacher, 'BrandNewPass123!')

        teacher.refresh_from_db()
        self.assertTrue(teacher.is_email_verified)

    def test_password_reset_confirm_invalid_token_rejects(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        invalid_url = reverse('accounts:password_reset_confirm', kwargs={'uidb64': uid, 'token': 'invalid-token-123'})

        response = self.client.get(invalid_url)
        self.assertEqual(response.status_code, 200)
        # Should render invalid token message / not display password fields
        self.assertContains(response, 'invalid')

        # Attempt to POST new password should not change password
        post_response = self.client.post(invalid_url, {
            'new_password1': 'NewValidPassword123!',
            'new_password2': 'NewValidPassword123!',
        })
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPassword123!'))

    @override_settings(PASSWORD_RESET_TIMEOUT=-1)
    def test_password_reset_confirm_expired_token_rejects(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        expired_url = reverse('accounts:password_reset_confirm', kwargs={'uidb64': uid, 'token': token})

        response = self.client.get(expired_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invalid')
        