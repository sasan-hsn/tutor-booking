from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class GrandfatherExistingUsersMigrationTests(TransactionTestCase):
    migrate_from = [("accounts", "0005_user_verification_fields_and_unique_constraint")]
    migrate_to = [("accounts", "0006_grandfather_existing_users")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        # Migrate backward to 0005 state
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        OldUser = old_apps.get_model("accounts", "User")

        # Create test users under 0005 state
        OldUser.objects.create(
            username="teacher_grandfather",
            email="Mary@EnglishWithMary.IR",
            is_email_verified=False,
        )
        OldUser.objects.create(
            username="student_grandfather",
            email="Student@Example.COM",
            is_email_verified=False,
        )
        OldUser.objects.create(
            username="no_email_user",
            email="",
            is_email_verified=False,
        )

        # Run forward migration to 0006
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        self.new_apps = self.executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        # Restore database to latest migrations
        self.executor.loader.build_graph()
        self.executor.migrate(self.executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_migration_normalizes_and_grandfathers_users(self):
        NewUser = self.new_apps.get_model("accounts", "User")
        u1 = NewUser.objects.get(username="teacher_grandfather")
        self.assertEqual(u1.email, "mary@englishwithmary.ir")
        self.assertTrue(u1.is_email_verified)

        u2 = NewUser.objects.get(username="student_grandfather")
        self.assertEqual(u2.email, "student@example.com")
        self.assertTrue(u2.is_email_verified)

        u3 = NewUser.objects.get(username="no_email_user")
        self.assertEqual(u3.email, "")
        self.assertFalse(u3.is_email_verified)

    def test_reverse_migration_resets_is_email_verified(self):
        # Run backward migration back to 0005
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_from)
        reverted_apps = self.executor.loader.project_state(self.migrate_from).apps
        RevertedUser = reverted_apps.get_model("accounts", "User")

        u1 = RevertedUser.objects.get(username="teacher_grandfather")
        self.assertFalse(u1.is_email_verified)

        u2 = RevertedUser.objects.get(username="student_grandfather")
        self.assertFalse(u2.is_email_verified)
