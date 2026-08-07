from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from .models import StudentProfile
from portfolio.models import TeacherProfile

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, **kwargs):
    if not created:
        return
    if instance.role == instance.Role.STUDENT:
        StudentProfile.objects.create(user=instance)
    elif instance.role == instance.Role.TEACHER:
        TeacherProfile.objects.create(user=instance)