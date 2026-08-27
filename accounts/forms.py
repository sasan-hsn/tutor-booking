from django.contrib.auth.forms import UserCreationForm
from django import forms
from .models import User


class StudentSignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.role = User.Role.STUDENT
        if commit:
            instance.save()
        return instance


class ProfileSettingsForm(forms.Form):
    profile_picture = forms.ImageField(required=False)

    def __init__(self, *args, profile=None, **kwargs):
        self.profile = profile
        super().__init__(*args, **kwargs)

    def save(self):
        if self.cleaned_data.get('profile_picture'):
            self.profile.profile_picture = self.cleaned_data['profile_picture']
            self.profile.save() 