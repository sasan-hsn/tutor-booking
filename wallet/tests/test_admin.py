from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from wallet.models import LedgerEntry

User = get_user_model()


class WalletAdminReadOnlyTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.superuser = User.objects.create_superuser(
            username="admin_superuser",
            email="admin@example.com",
            password="adminpassword",
        )
        self.staff_user = User.objects.create_user(
            username="admin_staff",
            email="staff@example.com",
            password="staffpassword",
            is_staff=True,
        )

    def test_ledger_entry_registered_and_strictly_read_only(self):
        self.assertIn(
            LedgerEntry,
            admin.site._registry,
            "LedgerEntry is not registered in django.contrib.admin",
        )
        model_admin = admin.site._registry[LedgerEntry]

        for user in [self.superuser, self.staff_user]:
            request = self.factory.get("/")
            request.user = user

            self.assertFalse(
                model_admin.has_add_permission(request),
                f"LedgerEntry allows add permission for {user.username}",
            )
            self.assertFalse(
                model_admin.has_change_permission(request),
                f"LedgerEntry allows change permission for {user.username}",
            )
            self.assertFalse(
                model_admin.has_delete_permission(request),
                f"LedgerEntry allows delete permission for {user.username}",
            )

        # Check list display and filters
        self.assertTrue(len(model_admin.list_display) > 0)
        self.assertIn("entry_type", model_admin.list_filter)
