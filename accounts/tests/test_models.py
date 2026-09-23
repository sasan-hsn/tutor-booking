from django.db import IntegrityError
from django.test import TestCase
from accounts.models import User


class UserModelTests(TestCase):
    def test_user_has_verification_fields_defaults(self):
        user = User.objects.create_user(
            username='testuser',
            password='password123',
            email='test@example.com',
        )
        self.assertFalse(user.is_email_verified)
        self.assertIsNone(user.pending_email)

    def test_email_normalized_to_lowercase_on_create(self):
        user = User.objects.create_user(
            username='testuser',
            password='password123',
            email='TestUser@EXAMPLE.COM',
        )
        self.assertEqual(user.email, 'testuser@example.com')
        user.refresh_from_db()
        self.assertEqual(user.email, 'testuser@example.com')

    def test_email_normalized_to_lowercase_on_save(self):
        user = User(
            username='testuser',
            email='ANOTHER@DOMAIN.ORG',
        )
        user.set_password('password123')
        user.save()
        self.assertEqual(user.email, 'another@domain.org')
        user.refresh_from_db()
        self.assertEqual(user.email, 'another@domain.org')

    def test_email_normalized_to_lowercase_on_update(self):
        user = User.objects.create_user(
            username='testuser',
            password='password123',
            email='original@example.com',
        )
        user.email = 'UPDATED@EXAMPLE.COM'
        user.save()
        self.assertEqual(user.email, 'updated@example.com')
        user.refresh_from_db()
        self.assertEqual(user.email, 'updated@example.com')

    def test_pending_email_normalized_to_lowercase(self):
        user = User.objects.create_user(
            username='testuser',
            password='password123',
            email='user@example.com',
        )
        user.pending_email = 'NewPending@EXAMPLE.COM'
        user.save()
        self.assertEqual(user.pending_email, 'newpending@example.com')
        user.refresh_from_db()
        self.assertEqual(user.pending_email, 'newpending@example.com')

    def test_empty_pending_email_normalized_to_none(self):
        user = User.objects.create_user(
            username='testuser',
            password='password123',
            email='user@example.com',
        )
        user.pending_email = '   '
        user.save()
        self.assertIsNone(user.pending_email)
        user.refresh_from_db()
        self.assertIsNone(user.pending_email)

    def test_case_insensitive_email_uniqueness_db_constraint(self):
        User.objects.create_user(
            username='user1',
            password='password123',
            email='unique@example.com',
        )
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                username='user2',
                password='password123',
                email='UNIQUE@EXAMPLE.COM',
            )

    def test_empty_emails_do_not_violate_unique_constraint(self):
        user1 = User.objects.create_user(
            username='empty1',
            password='password123',
            email='',
        )
        user2 = User.objects.create_user(
            username='empty2',
            password='password123',
            email='',
        )
        self.assertEqual(user1.email, '')
        self.assertEqual(user2.email, '')
