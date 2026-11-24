from decimal import Decimal

from django.conf import settings
from django.test import TestCase

from accounts.models import Tenant, User
from portfolio.models import TeacherProfile


class TenantProvisioningSignalTestCase(TestCase):
    def test_tenant_auto_provisioned_on_teacher_user_creation(self):
        user = User.objects.create_user(
            username="teacher_mary",
            password="testpassword",
            role=User.Role.TEACHER,
            first_name="Mary",
            last_name="Smith",
        )
        self.assertTrue(hasattr(user, "teacher_profile"))
        tenant = Tenant.objects.get(teacher_profile=user.teacher_profile)
        self.assertEqual(tenant.name, "Mary Smith")
        self.assertFalse(tenant.payments_enabled)
        self.assertEqual(tenant.default_currency, "USD")
        self.assertEqual(tenant.min_topup_amount, settings.MIN_TOPUP_AMOUNT)
        self.assertEqual(tenant.min_topup_amount, Decimal("20.00"))

    def test_tenant_auto_provisioned_on_direct_teacher_profile_creation(self):
        user = User.objects.create_user(
            username="teacher_direct",
            password="testpassword",
            role=User.Role.STUDENT,  # prevent auto profile from User post_save
        )
        profile = TeacherProfile.objects.create(user=user)
        tenant = Tenant.objects.get(teacher_profile=profile)
        self.assertFalse(tenant.payments_enabled)
        self.assertEqual(tenant.default_currency, "USD")
        self.assertEqual(tenant.min_topup_amount, settings.MIN_TOPUP_AMOUNT)

    def test_tenant_signal_idempotent_with_pre_existing_tenant(self):
        user = User.objects.create_user(
            username="teacher_pre",
            password="testpassword",
            role=User.Role.STUDENT,
        )
        profile = TeacherProfile(user=user)
        # Pre-create tenant manually before profile save fires signal
        profile.save()
        tenant = Tenant.objects.get(teacher_profile=profile)
        tenant.name = "Custom Storefront"
        tenant.save()

        # Fire post_save again manually or by saving profile
        profile.headline = "Updated headline"
        profile.save()

        # Reload tenant and verify no collision and name untouched
        tenant.refresh_from_db()
        self.assertEqual(tenant.name, "Custom Storefront")
        self.assertEqual(Tenant.objects.filter(teacher_profile=profile).count(), 1)
