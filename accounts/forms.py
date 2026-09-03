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
        else:
            del self.fields['instant_tutoring_enabled']

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
            self.profile.save()