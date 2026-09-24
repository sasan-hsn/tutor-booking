from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.db.models import Q

from .models import User
from .utils import get_timezone_choices


class EmailNormalizationAndUniquenessMixin:
    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data.get('email', ''))
        if email:
            qs = User.objects.filter(
                Q(email__iexact=email) | Q(pending_email__iexact=email)
            )
            user_instance = getattr(self, 'instance', None) or getattr(self, 'user', None)
            if user_instance and user_instance.pk:
                qs = qs.exclude(pk=user_instance.pk)
            if qs.exists():
                raise forms.ValidationError("A user with that email already exists.")
        return email


class StudentSignUpForm(EmailNormalizationAndUniquenessMixin, UserCreationForm):
    email = forms.EmailField(required=True)
    timezone = forms.ChoiceField(choices=[], required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['timezone'].choices = [(tz, tz) for tz in get_timezone_choices()]
        for name, field in self.fields.items():
            widget_class = 'form-select' if name == 'timezone' else 'form-control'
            field.widget.attrs.update({'class': widget_class})

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.role = User.Role.STUDENT
        tz = self.cleaned_data.get('timezone')
        if tz:
            instance.timezone = tz
        if commit:
            instance.save()
        return instance


class StyledAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})


class StyledPasswordResetForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})

    def save(self, commit=True):
        if not self.user.is_email_verified:
            self.user.is_email_verified = True
        return super().save(commit=commit)


class StudentProfileSettingsForm(EmailNormalizationAndUniquenessMixin, forms.Form):
    email = forms.EmailField(required=True)
    profile_picture = forms.ImageField(required=False)
    timezone = forms.ChoiceField(choices=[], required=False)

    def __init__(self, *args, profile=None, user=None, **kwargs):
        self.profile = profile
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['timezone'].choices = [(tz, tz) for tz in get_timezone_choices()]
        if user:
            self.fields['email'].initial = user.email
            self.fields['timezone'].initial = user.timezone
        self.fields['email'].widget.attrs.update({'class': 'form-control'})
        self.fields['timezone'].widget.attrs.update({'class': 'form-select'})
        self.fields['profile_picture'].widget.attrs.update({'class': 'form-control'})

    def save(self):
        if self.cleaned_data.get('profile_picture') and self.profile:
            self.profile.profile_picture = self.cleaned_data['profile_picture']
            self.profile.save()
        tz = self.cleaned_data.get('timezone')
        if tz and self.user:
            self.user.timezone = tz

        email = self.cleaned_data.get('email')
        email_changed = False
        if email and self.user:
            current_email = (self.user.email or '').strip().lower()
            if email != current_email:
                if (self.user.pending_email or '').strip().lower() != email:
                    self.user.pending_email = email
                    email_changed = True

        if self.user:
            self.user.save()

        return email_changed


class TeacherSignUpForm(EmailNormalizationAndUniquenessMixin, UserCreationForm):
    email = forms.EmailField(required=True)
    timezone = forms.ChoiceField(choices=[], required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['timezone'].choices = [(tz, tz) for tz in get_timezone_choices()]
        for name, field in self.fields.items():
            widget_class = 'form-select' if name == 'timezone' else 'form-control'
            field.widget.attrs.update({'class': widget_class})

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.role = User.Role.TEACHER
        tz = self.cleaned_data.get('timezone')
        if tz:
            instance.timezone = tz
        if commit:
            instance.save()
        return instance