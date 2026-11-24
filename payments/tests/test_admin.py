from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from accounts.models import Tenant, TermsAcceptance
from payments.models import (
    Order,
    Payment,
    PaymentAddress,
    Payout,
    TenantPaymentAccount,
)
from wallet.models import Wallet

User = get_user_model()


class ReadOnlyAdminTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.superuser = User.objects.create_superuser(
            username="admin_user",
            email="admin@example.com",
            password="adminpassword",
        )
        self.staff_user = User.objects.create_user(
            username="staff_user",
            email="staff@example.com",
            password="staffpassword",
            is_staff=True,
        )

    def test_financial_models_registered_and_read_only(self):
        models_to_test = [
            Tenant,
            PaymentAddress,
            TermsAcceptance,
            Wallet,
            Order,
            Payment,
            Payout,
            TenantPaymentAccount,
        ]

        for model in models_to_test:
            with self.subTest(model=model.__name__):
                self.assertIn(
                    model,
                    admin.site._registry,
                    f"{model.__name__} is not registered in django.contrib.admin",
                )
                model_admin = admin.site._registry[model]

                for user in [self.superuser, self.staff_user]:
                    request = self.factory.get("/")
                    request.user = user

                    self.assertFalse(
                        model_admin.has_add_permission(request),
                        f"{model.__name__} allows add permission for {user.username}",
                    )
                    self.assertFalse(
                        model_admin.has_change_permission(request),
                        f"{model.__name__} allows change permission for {user.username}",
                    )
                    self.assertFalse(
                        model_admin.has_delete_permission(request),
                        f"{model.__name__} allows delete permission for {user.username}",
                    )
