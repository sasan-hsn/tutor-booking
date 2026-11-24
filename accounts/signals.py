from decimal import Decimal

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from portfolio.models import TeacherProfile

from .models import StudentProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, **kwargs):
    if not created:
        return

    if instance.role == instance.Role.STUDENT:
        StudentProfile.objects.get_or_create(user=instance)
    elif instance.role == instance.Role.TEACHER:
        TeacherProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=TeacherProfile)
def create_tenant_for_teacher(sender, instance, created, **kwargs):
    from .models import Tenant

    tenant_name = (
        instance.user.get_full_name()
        or instance.user.username
        or f"Teacher {instance.id}"
    )
    Tenant.objects.get_or_create(
        teacher_profile=instance,
        defaults={
            "name": tenant_name,
            "payments_enabled": False,
            "default_currency": "USD",
            "min_topup_amount": getattr(settings, "MIN_TOPUP_AMOUNT", Decimal("20.00")),
        },
    )
