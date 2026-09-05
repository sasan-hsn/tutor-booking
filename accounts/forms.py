from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django import forms
from .models import User
from .utils import get_timezone_choices


class StudentSignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)
    timezone = forms.ChoiceField(choices=[], required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['timezone'].choices = [(tz, tz) for tz in get_timezone_choices()]
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})

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


class ProfileSettingsForm(forms.Form):
    profile_picture = forms.ImageField(required=False)
    timezone = forms.ChoiceField(choices=[], required=False)
    instant_tutoring_enabled = forms.BooleanField(required=False)
    contact_email = forms.EmailField(required=False)
    whatsapp_number = forms.CharField(required=False, max_length=20)
    telegram_username = forms.CharField(required=False, max_length=64)
    instagram_username = forms.CharField(required=False, max_length=64)

    def __init__(self, *args, profile=None, user=None, **kwargs):
        self.profile = profile
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['timezone'].choices = [(tz, tz) for tz in get_timezone_choices()]
        if user:
            self.fields['timezone'].initial = user.timezone
        self.fields['timezone'].widget.attrs.update({'class': 'form-select'})
        self.fields['profile_picture'].widget.attrs.update({'class': 'form-control'})

        is_teacher = user and user.role == User.Role.TEACHER
        if is_teacher:
            self.fields['instant_tutoring_enabled'].initial = profile.instant_tutoring_enabled
            self.fields['instant_tutoring_enabled'].widget.attrs.update({
                'class': 'form-check-input',
                'role': 'switch',
            })
            self.fields['instant_tutoring_enabled'].label = 'Instant Tutoring (allow same-day bookings)'

            self.fields['contact_email'].initial = profile.contact_email
            self.fields['contact_email'].widget.attrs.update({'class': 'form-control', 'placeholder': 'you@example.com'})
            self.fields['contact_email'].label = 'Contact Email'

            self.fields['whatsapp_number'].initial = profile.whatsapp_number
            self.fields['whatsapp_number'].widget.attrs.update({'class': 'form-control', 'placeholder': '+1234567890'})
            self.fields['whatsapp_number'].label = 'WhatsApp Number'
            self.fields['whatsapp_number'].help_text = 'Include country code, e.g. +98912xxxxxxx'

            self.fields['telegram_username'].initial = profile.telegram_username
            self.fields['telegram_username'].widget.attrs.update({'class': 'form-control', 'placeholder': 'username'})
            self.fields['telegram_username'].label = 'Telegram Username'

            self.fields['instagram_username'].initial = profile.instagram_username
            self.fields['instagram_username'].widget.attrs.update({'class': 'form-control', 'placeholder': 'username'})
            self.fields['instagram_username'].label = 'Instagram Username'
        else:
            del self.fields['instant_tutoring_enabled']
            del self.fields['contact_email']
            del self.fields['whatsapp_number']
            del self.fields['telegram_username']
            del self.fields['instagram_username']

    def save(self):
        if self.cleaned_data.get('profile_picture'):
            self.profile.profile_picture = self.cleaned_data['profile_picture']
            self.profile.save()
        tz = self.cleaned_data.get('timezone')
        if tz and self.user:
            self.user.timezone = tz
            self.user.save()
        if 'instant_tutoring_enabled' in self.cleaned_data:
            self.profile.instant_tutoring_enabled = self.cleaned_data['instant_tutoring_enabled']
            self.profile.contact_email = self.cleaned_data['contact_email']
            self.profile.whatsapp_number = self.cleaned_data['whatsapp_number']
            self.profile.telegram_username = self.cleaned_data['telegram_username']
            self.profile.instagram_username = self.cleaned_data['instagram_username']
            self.profile.save()