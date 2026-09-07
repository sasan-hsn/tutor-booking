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