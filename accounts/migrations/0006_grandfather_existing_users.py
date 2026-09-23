# Generated manually for issue #182 data migration

from django.db import migrations


def grandfather_existing_users(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    for user in User.objects.all():
        if user.email and user.email.strip():
            user.email = user.email.strip().lower()
            user.is_email_verified = True
        else:
            user.email = ''
            user.is_email_verified = False
        user.save(update_fields=['email', 'is_email_verified'])


def reverse_grandfather_existing_users(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.all().update(is_email_verified=False)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_user_verification_fields_and_unique_constraint"),
    ]

    operations = [
        migrations.RunPython(
            grandfather_existing_users,
            reverse_code=reverse_grandfather_existing_users,
        ),
    ]
