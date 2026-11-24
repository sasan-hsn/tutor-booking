from decimal import Decimal

from django.conf import settings
from django.db import migrations


def provision_existing_teacher_tenants(apps, schema_editor):
    TeacherProfile = apps.get_model("portfolio", "TeacherProfile")
    Tenant = apps.get_model("accounts", "Tenant")
    min_topup = getattr(settings, "MIN_TOPUP_AMOUNT", Decimal("20.00"))

    for profile in TeacherProfile.objects.select_related("user").all():
        user = profile.user
        name_parts = []
        if user.first_name:
            name_parts.append(user.first_name)
        if user.last_name:
            name_parts.append(user.last_name)
        full_name = " ".join(name_parts).strip()
        tenant_name = full_name or user.username or f"Teacher {profile.id}"

        Tenant.objects.get_or_create(
            teacher_profile=profile,
            defaults={
                "name": tenant_name,
                "payments_enabled": False,
                "default_currency": "USD",
                "min_topup_amount": min_topup,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_tenant_termsacceptance"),
        ("portfolio", "0011_teacherprofile_lesson_price_25"),
        ("payments", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            provision_existing_teacher_tenants,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
