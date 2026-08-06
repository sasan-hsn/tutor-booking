from django.db import models

from django.conf import settings


class TeacherProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='teacher_profile'
    )
    bio = models.TextField(blank=True, default='')
    teaching_philosophy = models.TextField(blank=True, default='')
    intro_video_url = models.URLField(max_length=500, blank=True, default='')

    lesson_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    lesson_duration_minutes = models.PositiveSmallIntegerField(default=50)
    offers_trial = models.BooleanField(default=True)
    trial_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Teacher Profile: {self.user}"


class Certificate(models.Model):
    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.CASCADE,
        related_name='certificates'
    )
    title = models.CharField(max_length=255)
    issued_by = models.CharField(max_length=255)
    issue_date = models.DateField(null=True, blank=True)
    certificate_number = models.CharField(max_length=100, blank=True, default='')
    credential_url = models.URLField(max_length=500, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} - {self.teacher.user}"        