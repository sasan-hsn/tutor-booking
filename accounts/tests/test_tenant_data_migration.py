from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ProvisionExistingTeacherTenantsMigrationTests(TransactionTestCase):
    migrate_from = [("accounts", "0007_tenant_termsacceptance")]
    migrate_to = [("accounts", "0008_provision_existing_teacher_tenants")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        # Migrate to 0007 state
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        OldUser = old_apps.get_model("accounts", "User")
        OldTeacherProfile = old_apps.get_model("portfolio", "TeacherProfile")
        OldTenant = old_apps.get_model("accounts", "Tenant")

        # Create user and teacher profile without tenant
        self.teacher_user = OldUser.objects.create_user(
            username="existing_teacher",
            password="testpassword",
            role="teacher",
            first_name="Mary",
            last_name="Smith",
        )
        self.profile, _ = OldTeacherProfile.objects.get_or_create(
            user=self.teacher_user
        )
        # Delete any tenant so we test migration provisioning
        OldTenant.objects.filter(teacher_profile=self.profile).delete()
        self.assertFalse(
            OldTenant.objects.filter(teacher_profile=self.profile).exists()
        )

        # Run forward migration to 0008
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        self.new_apps = self.executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        # Restore database to latest migrations
        self.executor.loader.build_graph()
        self.executor.migrate(self.executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_migration_provisions_tenant_for_existing_teacher(self):
        NewTenant = self.new_apps.get_model("accounts", "Tenant")
        NewTeacherProfile = self.new_apps.get_model("portfolio", "TeacherProfile")
        NewPaymentAddress = self.new_apps.get_model("payments", "PaymentAddress")

        profile = NewTeacherProfile.objects.get(user__username="existing_teacher")
        tenant = NewTenant.objects.get(teacher_profile=profile)

        self.assertFalse(tenant.payments_enabled)
        self.assertEqual(tenant.default_currency, "USD")
        self.assertEqual(tenant.min_topup_amount, Decimal("20.00"))
        self.assertEqual(tenant.name, "Mary Smith")

        # Verify zero associated PaymentAddress records
        self.assertEqual(NewPaymentAddress.objects.filter(tenant=tenant).count(), 0)

    def test_reverse_migration_is_noop(self):
        # Migrate backward to 0007 state
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_from)
        reverted_apps = self.executor.loader.project_state(self.migrate_from).apps
        RevertedTenant = reverted_apps.get_model("accounts", "Tenant")
        # Reverse migration with noop leaves existing tenants intact and succeeds without error
        self.assertTrue(RevertedTenant.objects.filter(name="Mary Smith").exists())
