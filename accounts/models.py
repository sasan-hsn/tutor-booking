from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager as BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from zoneinfo import available_timezones


def validate_timezone(value):
    if value not in available_timezones():
        raise ValidationError(f'{value} is not a valid timezone.')


class UserManager(BaseUserManager):
    @classmethod
    def normalize_email(cls, email):
        email = email or ""
        return email.strip().lower()


class User(AbstractUser):
    class Role(models.TextChoices):
        TEACHER = "teacher", "Teacher"
        STUDENT = "student", "Student"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STUDENT)
    timezone = models.CharField(max_length=64, default='UTC', validators=[validate_timezone])
    is_email_verified = models.BooleanField(default=False)
    pending_email = models.EmailField(blank=True, null=True)

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                Lower('email'),
                name='unique_lower_email',
                condition=~models.Q(email=''),
            ),
        ]

    def clean(self):
        super().clean()
        if self.email and self.email.strip():
            self.email = self.email.strip().lower()
        else:
            self.email = ''
        if self.pending_email and self.pending_email.strip():
            self.pending_email = self.pending_email.strip().lower()
        else:
            self.pending_email = None

    def save(self, *args, **kwargs):
        if self.email and self.email.strip():
            self.email = self.email.strip().lower()
        else:
            self.email = ''
        if self.pending_email and self.pending_email.strip():
            self.pending_email = self.pending_email.strip().lower()
        else:
            self.pending_email = None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.get_full_name() or self.username


class StudentProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='student_profile',
    )
    profile_picture = models.ImageField(upload_to="profile_pictures/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Student Profile: {self.user.username}"