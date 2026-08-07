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