from django.contrib.auth import get_user_model
from django.core.signing import TimestampSigner
from django.test import TestCase

from accounts.tokens import EmailVerificationTokenGenerator, email_verification_token_generator

User = get_user_model()


class EmailVerificationTokenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='tokenuser',
            email='tokenuser@example.com',
            password='InitialPassword123!',
        )
        self.generator = email_verification_token_generator

    def test_make_token_structure(self):
        token = self.generator.make_token(self.user)
        self.assertIsInstance(token, str)
        parts = token.split(':')
        self.assertEqual(len(parts), 3, "Token must consist of payload, timestamp, and signature.")

    def test_check_token_valid(self):
        token = self.generator.make_token(self.user)
        user, status = self.generator.check_token(token)
        self.assertEqual(status, 'valid')
        self.assertEqual(user, self.user)

    def test_check_token_expired(self):
        # Create a custom generator with 0-second TTL to test expiration
        short_generator = EmailVerificationTokenGenerator()
        short_generator.TOKEN_TTL = -1
        token = short_generator.make_token(self.user)

        user, status = short_generator.check_token(token)
        self.assertEqual(status, 'expired')
        self.assertEqual(user, self.user)

    def test_check_token_tampered_signature(self):
        token = self.generator.make_token(self.user)
        parts = token.split(':')
        tampered_token = f"{parts[0]}:{parts[1]}:{parts[2]}extra"
        user, status = self.generator.check_token(tampered_token)
        self.assertEqual(status, 'invalid')
        self.assertIsNone(user)

    def test_check_token_tampered_payload(self):
        token = self.generator.make_token(self.user)
        parts = token.split(':')
        tampered_token = f"badpayload:{parts[1]}:{parts[2]}"
        user, status = self.generator.check_token(tampered_token)
        self.assertEqual(status, 'invalid')
        self.assertIsNone(user)

    def test_check_token_password_change_invalidates(self):
        token = self.generator.make_token(self.user)
        # Verify valid before password change
        user, status = self.generator.check_token(token)
        self.assertEqual(status, 'valid')

        # Change password and save
        self.user.set_password('NewPassword456!')
        self.user.save()

        # Outstanding token must now be invalid
        user, status = self.generator.check_token(token)
        self.assertEqual(status, 'invalid')
        self.assertIsNone(user)

    def test_check_token_email_mismatch_invalidates(self):
        token = self.generator.make_token(self.user)
        self.user.email = 'different@example.com'
        self.user.save()

        user, status = self.generator.check_token(token)
        self.assertEqual(status, 'invalid')
        self.assertIsNone(user)

    def test_check_token_nonexistent_user(self):
        token = self.generator.make_token(self.user)
        self.user.delete()

        user, status = self.generator.check_token(token)
        self.assertEqual(status, 'invalid')
        self.assertIsNone(user)

    def test_check_token_invalid_inputs(self):
        for bad_input in [None, '', 'not-a-token', 'a:b', 12345]:
            user, status = self.generator.check_token(bad_input)
            self.assertEqual(status, 'invalid')
            self.assertIsNone(user)
