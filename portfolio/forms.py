from django import forms
from accounts.utils import get_timezone_choices
from accounts.models import User
from .models import TeacherProfile, Certificate



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
            if name == 'profile_picture':
                field.widget.attrs.update({'class': 'form-control'})
            elif name == 'timezone':
                field.widget.attrs.update({'class': 'form-select'})
            else:
                field.widget.attrs.update({'class': 'form-control'})

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.exclude(pk=self.user.pk).filter(username=username).exists():
            raise forms.ValidationError('This username is already taken.')
        return username

    def save(self):
        self.user.username = self.cleaned_data['username']
        self.user.first_name = self.cleaned_data['first_name']
        self.user.last_name = self.cleaned_data['last_name']
        self.user.timezone = self.cleaned_data['timezone']
        self.user.save()
        if self.cleaned_data.get('profile_picture'):
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
            'lesson_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'lesson_duration_minutes': forms.NumberInput(attrs={'class': 'form-control'}),
            'offers_trial': forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
            'trial_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'trial_duration_minutes': forms.NumberInput(attrs={'class': 'form-control'}),
            'instant_tutoring_enabled': forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
        }