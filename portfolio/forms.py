from decimal import Decimal
from django import forms
from accounts.models import User
from accounts.utils import get_timezone_choices
from .models import Certificate, TeacherProfile


class TeacherAccountSettingsForm(forms.Form):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    profile_picture = forms.ImageField(required=False)
    timezone = forms.ChoiceField(choices=[])

    def __init__(self, *args, user=None, profile=None, **kwargs):
        self.user = user
        self.profile = profile
        super().__init__(*args, **kwargs)
        self.fields['timezone'].choices = [(tz, tz) for tz in get_timezone_choices()]

        if user:
            self.fields['username'].initial = user.username
            self.fields['first_name'].initial = user.first_name
            self.fields['last_name'].initial = user.last_name
            self.fields['timezone'].initial = user.timezone

        for name, field in self.fields.items():
            widget_class = 'form-select' if name == 'timezone' else 'form-control'
            field.widget.attrs.update({'class': widget_class})

    def clean_username(self):
        username = self.cleaned_data['username']
        if self.user and User.objects.exclude(pk=self.user.pk).filter(username=username).exists():
            raise forms.ValidationError('This username is already taken.')
        return username

    def save(self):
        if self.user:
            self.user.username = self.cleaned_data['username']
            self.user.first_name = self.cleaned_data['first_name']
            self.user.last_name = self.cleaned_data['last_name']
            self.user.timezone = self.cleaned_data['timezone']
            self.user.save()

        if self.profile and self.cleaned_data.get('profile_picture'):
            self.profile.profile_picture = self.cleaned_data['profile_picture']
            self.profile.save()


class TeacherPortfolioSettingsForm(forms.ModelForm):
    class Meta:
        model = TeacherProfile
        fields = [
            'headline', 'bio', 'teaching_philosophy', 'intro_video_url',
            'contact_email', 'whatsapp_number', 'telegram_username', 'instagram_username',
        ]
        widgets = {
            'headline': forms.TextInput(attrs={'class': 'form-control'}),
            'bio': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'teaching_philosophy': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'intro_video_url': forms.URLInput(attrs={'class': 'form-control'}),
            'contact_email': forms.EmailInput(attrs={'class': 'form-control'}),
            'whatsapp_number': forms.TextInput(attrs={'class': 'form-control'}),
            'telegram_username': forms.TextInput(attrs={'class': 'form-control'}),
            'instagram_username': forms.TextInput(attrs={'class': 'form-control'}),
        }


class CertificateForm(forms.ModelForm):
    class Meta:
        model = Certificate
        fields = ['title', 'issued_by', 'issue_date', 'certificate_number', 'credential_url']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Certificate title'}),
            'issued_by': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Issued by'}),
            'issue_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'certificate_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'credential_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'Optional link'}),
        }


class TeacherBookingSettingsForm(forms.ModelForm):
    class Meta:
        model = TeacherProfile
        fields = [
            'lesson_price', 'lesson_duration_minutes',
            'offers_trial', 'trial_price', 'trial_duration_minutes',
            'instant_tutoring_enabled',
        ]
        widgets = {
            'lesson_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'lesson_duration_minutes': forms.NumberInput(attrs={'class': 'form-control', 'min': '15'}),
            'offers_trial': forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
            'trial_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'trial_duration_minutes': forms.NumberInput(attrs={'class': 'form-control', 'min': '15'}),
            'instant_tutoring_enabled': forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        offers_trial = cleaned_data.get('offers_trial')
        trial_price = cleaned_data.get('trial_price')

        # If trial is offered, ensure trial_price is not negative
        if offers_trial and trial_price is not None and trial_price < Decimal('0.00'):
            self.add_error('trial_price', 'Trial price cannot be negative.')

        lesson_price = cleaned_data.get('lesson_price')
        if lesson_price is not None and lesson_price < Decimal('0.00'):
            self.add_error('lesson_price', 'Lesson price cannot be negative.')

        return cleaned_data