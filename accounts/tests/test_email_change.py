import smtplib
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.tokens import (
    EmailChangeRevocationTokenGenerator,
    EmailChangeTokenGenerator,
    email_change_revocation_token_generator,
    email_change_token_generator,
)

User = get_user_model()


class EmailChangeProfileSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            username='student_change',
            email='student_old@example.com',
            password='Password123!',
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.teacher = User.objects.create_user(
            username='teacher_change',
            email='teacher_old@example.com',
            password='Password123!',
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.other_user = User.objects.create_user(
            username='other_user',
            email='other@example.com',
            pending_email='pending_other@example.com',
            password='Password123!',
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

    def tearDown(self):
        cache.clear()

    def test_student_profile_settings_displays_active_email_and_verified_badge(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('accounts:profile_settings'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'student_old@example.com')
        self.assertContains(response, 'Verified')

    def test_teacher_account_settings_displays_active_email_and_verified_badge(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse('portfolio:teacher_settings_account'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'teacher_old@example.com')
        self.assertContains(response, 'Verified')

    def test_student_profile_settings_renders_pending_alert_subpanel(self):
        self.student.pending_email = 'student_pending@example.com'
        self.student.save()
        self.client.force_login(self.student)

        response = self.client.get(reverse('accounts:profile_settings'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pending confirmation:')
        self.assertContains(response, 'student_pending@example.com')
        self.assertContains(response, 'Resend')
        self.assertContains(response, 'Cancel Request')

    def test_teacher_account_settings_renders_pending_alert_subpanel(self):
        self.teacher.pending_email = 'teacher_pending@example.com'
        self.teacher.save()
        self.client.force_login(self.teacher)

        response = self.client.get(reverse('portfolio:teacher_settings_account'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pending confirmation:')
        self.assertContains(response, 'teacher_pending@example.com')
        self.assertContains(response, 'Resend')
        self.assertContains(response, 'Cancel Request')

    def test_submitting_new_email_student_sets_pending_and_dispatches_emails(self):
        self.client.force_login(self.student)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('accounts:profile_settings'), {
                'email': 'student_new@example.com',
                'timezone': 'UTC',
            })
        self.assertRedirects(response, reverse('accounts:profile_settings'))

        self.student.refresh_from_db()
        # Active email remains unchanged!
        self.assertEqual(self.student.email, 'student_old@example.com')
        # Pending email holds the new target
        self.assertEqual(self.student.pending_email, 'student_new@example.com')

        # Two emails sent: confirmation to new, advisory to old
        self.assertEqual(len(mail.outbox), 2)
        recipients = {m.to[0]: m for m in mail.outbox}
        self.assertIn('student_new@example.com', recipients)
        self.assertIn('student_old@example.com', recipients)

        # Check confirmation email
        confirm_email = recipients['student_new@example.com']
        self.assertIn('Confirm your new email address', confirm_email.subject)
        self.assertIn('/accounts/email-change/confirm/', confirm_email.body)

        # Check advisory email
        advisory_email = recipients['student_old@example.com']
        self.assertIn('Security Alert: Email address change requested', advisory_email.subject)
        self.assertIn('/accounts/email-change/revoke/', advisory_email.body)

    def test_submitting_new_email_teacher_sets_pending_and_dispatches_emails(self):
        self.client.force_login(self.teacher)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('portfolio:teacher_settings_account'), {
                'username': self.teacher.username,
                'email': 'teacher_new@example.com',
                'first_name': 'Mary',
                'last_name': 'Smith',
                'timezone': 'UTC',
            })
        self.assertRedirects(response, reverse('portfolio:teacher_settings_account'))

        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.email, 'teacher_old@example.com')
        self.assertEqual(self.teacher.pending_email, 'teacher_new@example.com')

        self.assertEqual(len(mail.outbox), 2)
        recipients = {m.to[0]: m for m in mail.outbox}
        self.assertIn('teacher_new@example.com', recipients)
        self.assertIn('teacher_old@example.com', recipients)

    def test_competing_claim_on_active_or_pending_email_rejected(self):
        self.client.force_login(self.student)
        # Attempt to claim active email of other_user
        response = self.client.post(reverse('accounts:profile_settings'), {
            'email': 'OTHER@EXAMPLE.COM',
            'timezone': 'UTC',
        })
        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.pending_email)
        self.assertContains(response, 'A user with that email already exists.')

        # Attempt to claim pending email of other_user
        response = self.client.post(reverse('accounts:profile_settings'), {
            'email': 'PENDING_OTHER@EXAMPLE.COM',
            'timezone': 'UTC',
        })
        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.pending_email)
        self.assertContains(response, 'A user with that email already exists.')

    def test_cancel_button_clears_pending_email_immediately(self):
        self.student.pending_email = 'student_pending@example.com'
        self.student.save()
        self.client.force_login(self.student)

        token = email_change_token_generator.make_token(self.student)
        user, status = email_change_token_generator.check_token(token)
        self.assertEqual(status, 'valid')

        response = self.client.post(reverse('accounts:cancel_email_change'))
        self.assertRedirects(response, reverse('accounts:profile_settings'))

        self.student.refresh_from_db()
        self.assertIsNone(self.student.pending_email)

        # Outstanding token is now immediately invalidated
        user, status = email_change_token_generator.check_token(token)
        self.assertEqual(status, 'invalid')
        self.assertIsNone(user)


class EmailChangeConfirmationTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username='confirm_student',
            email='old@example.com',
            pending_email='new@example.com',
            password='Password123!',
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.teacher = User.objects.create_user(
            username='confirm_teacher',
            email='teach_old@example.com',
            pending_email='teach_new@example.com',
            password='Password123!',
            role=User.Role.TEACHER,
            is_email_verified=True,
        )

    def test_confirm_email_logged_out_auto_logins_and_swaps_email(self):
        token = email_change_token_generator.make_token(self.student)
        url = reverse('accounts:confirm_email_change', kwargs={'token': token})

        response = self.client.get(url)
        self.assertRedirects(response, reverse('accounts:profile_settings'))

        self.student.refresh_from_db()
        self.assertEqual(self.student.email, 'new@example.com')
        self.assertIsNone(self.student.pending_email)
        self.assertTrue(self.student.is_email_verified)

        # Verified auto-login session
        self.assertEqual(int(self.client.session['_auth_user_id']), self.student.pk)

    def test_confirm_email_teacher_redirects_to_teacher_settings(self):
        token = email_change_token_generator.make_token(self.teacher)
        url = reverse('accounts:confirm_email_change', kwargs={'token': token})

        response = self.client.get(url)
        self.assertRedirects(response, reverse('portfolio:teacher_settings_account'))

        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.email, 'teach_new@example.com')
        self.assertIsNone(self.teacher.pending_email)
        self.assertTrue(self.teacher.is_email_verified)

    def test_confirm_email_cross_session_conflict_renders_interception_screen(self):
        # Teacher is logged in, but clicks confirmation link for Student
        self.client.force_login(self.teacher)
        token = email_change_token_generator.make_token(self.student)
        url = reverse('accounts:confirm_email_change', kwargs={'token': token})

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/verify_email_conflict.html')
        self.assertContains(response, 'Account Session Conflict')

        # Emails have not swapped
        self.student.refresh_from_db()
        self.assertEqual(self.student.email, 'old@example.com')
        self.assertEqual(self.student.pending_email, 'new@example.com')

    def test_confirm_email_expired_token(self):
        token = email_change_token_generator.make_token(self.student)
        url = reverse('accounts:confirm_email_change', kwargs={'token': token})

        with patch.object(email_change_token_generator, 'TOKEN_TTL', -1):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'expired', status_code=400)

        self.student.refresh_from_db()
        self.assertEqual(self.student.email, 'old@example.com')

    def test_confirm_email_tampered_token(self):
        token = email_change_token_generator.make_token(self.student)
        tampered = f"{token}tampered"
        url = reverse('accounts:confirm_email_change', kwargs={'token': tampered})

        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Invalid', status_code=400)

        self.student.refresh_from_db()
        self.assertEqual(self.student.email, 'old@example.com')

    def test_confirm_email_aborted_if_competing_claim_occurred(self):
        # Suppose another account verified or took 'new@example.com' before this confirmation was clicked
        User.objects.create_user(
            username='thief',
            email='new@example.com',
            password='Password123!',
        )
        token = email_change_token_generator.make_token(self.student)
        url = reverse('accounts:confirm_email_change', kwargs={'token': token})

        response = self.client.get(url)
        self.assertRedirects(response, reverse('accounts:profile_settings'))

        self.student.refresh_from_db()
        self.assertEqual(self.student.email, 'old@example.com')
        self.assertIsNone(self.student.pending_email)


class EmailChangeRevocationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='revoke_test_user',
            email='active@example.com',
            pending_email='malicious@example.com',
            password='Password123!',
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

    def test_revocation_link_get_renders_confirmation_screen_without_cancelling(self):
        # Simulates corporate anti-malware pre-fetcher (e.g. SafeLinks) visiting link via GET
        token = email_change_revocation_token_generator.make_token(self.user)
        url = reverse('accounts:revoke_email_change', kwargs={'token': token})

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/revoke_email_change.html')
        self.assertContains(response, 'Cancel Email Change Request')
        self.assertContains(response, 'malicious@example.com')

        # Invariant check: pending_email MUST still be set!
        self.user.refresh_from_db()
        self.assertEqual(self.user.pending_email, 'malicious@example.com')

    def test_revocation_post_terminates_pending_change(self):
        token = email_change_revocation_token_generator.make_token(self.user)
        url = reverse('accounts:revoke_email_change', kwargs={'token': token})

        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/revoke_email_change_success.html')
        self.assertContains(response, 'Email Change Request Cancelled')

        self.user.refresh_from_db()
        self.assertIsNone(self.user.pending_email)
        self.assertEqual(self.user.email, 'active@example.com')

        # Further attempts to revoke with the same token fail
        response_retry = self.client.get(url)
        self.assertEqual(response_retry.status_code, 400)
        self.assertTemplateUsed(response_retry, 'accounts/revoke_email_change_invalid.html')

    def test_revocation_expired_token(self):
        token = email_change_revocation_token_generator.make_token(self.user)
        url = reverse('accounts:revoke_email_change', kwargs={'token': token})

        with patch.object(email_change_revocation_token_generator, 'TOKEN_TTL', -1):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, 'accounts/revoke_email_change_invalid.html')
        self.assertContains(response, 'expired', status_code=400)


class EmailChangeResendAndTasksTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='resend_user',
            email='active@example.com',
            pending_email='pending@example.com',
            password='Password123!',
            role=User.Role.STUDENT,
            is_email_verified=True,
        )

    def tearDown(self):
        cache.clear()

    def test_resend_endpoint_dispatches_confirmation_email(self):
        self.client.force_login(self.user)
        with patch('accounts.views.safe_send_email_change_confirmation_email') as mock_send:
            response = self.client.post(reverse('accounts:resend_email_change_email'))
        self.assertRedirects(response, reverse('accounts:profile_settings'))
        mock_send.assert_called_once_with(self.user.pk)

    def test_resend_rate_limit_cooldown(self):
        self.client.force_login(self.user)
        # First request succeeds
        with patch('accounts.views.safe_send_email_change_confirmation_email'):
            self.client.post(reverse('accounts:resend_email_change_email'))

        # Immediate second request triggers 60s cooldown
        with patch('accounts.views.safe_send_email_change_confirmation_email') as mock_send:
            response = self.client.post(reverse('accounts:resend_email_change_email'))
        mock_send.assert_not_called()
        self.assertRedirects(response, reverse('accounts:profile_settings'))

    def test_send_email_change_emails_task_retries_on_transient_error(self):
        from accounts.tasks import send_email_change_emails_task

        with patch('accounts.emails.send_email_change_confirmation_email', side_effect=smtplib.SMTPException("Transient")):
            with patch.object(send_email_change_emails_task, 'retry', side_effect=Exception("TaskRetried")) as mock_retry:
                with self.assertRaises(Exception) as cm:
                    send_email_change_emails_task(self.user.pk)
                self.assertIn("TaskRetried", str(cm.exception))
                mock_retry.assert_called_once()
