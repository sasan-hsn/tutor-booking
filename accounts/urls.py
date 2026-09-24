from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .forms import StyledPasswordResetForm, StyledSetPasswordForm

app_name = 'accounts'
urlpatterns = [
    path('signup/', views.student_signup, name='student_signup'),
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('settings/', views.profile_settings, name='profile_settings'),
    path('signup/teacher/', views.teacher_signup, name='teacher_signup'),
    path('verify/resend/', views.resend_verification_email, name='resend_verification_email'),
    path('resend-verification/', views.resend_verification_email),
    path('verify-email/<str:token>/', views.verify_email, name='verify_email'),
    path('verify/<str:token>/', views.verify_email),
    path('email-change/confirm/<str:token>/', views.confirm_email_change, name='confirm_email_change'),
    path('email-change/revoke/<str:token>/', views.revoke_email_change, name='revoke_email_change'),
    path('email-change/cancel/', views.cancel_email_change, name='cancel_email_change'),
    path('email-change/resend/', views.resend_email_change_email, name='resend_email_change_email'),
    path(
        'password_reset/',
        auth_views.PasswordResetView.as_view(
            template_name='accounts/password_reset_form.html',
            email_template_name='accounts/password_reset_email.txt',
            html_email_template_name='accounts/password_reset_email.html',
            subject_template_name='accounts/password_reset_subject.txt',
            extra_email_context={'site_name': 'English with Mary'},
            success_url=reverse_lazy('accounts:password_reset_done'),
            form_class=StyledPasswordResetForm,
        ),
        name='password_reset',
    ),
    path(
        'password_reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='accounts/password_reset_done.html',
        ),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='accounts/password_reset_confirm.html',
            success_url=reverse_lazy('accounts:password_reset_complete'),
            form_class=StyledSetPasswordForm,
        ),
        name='password_reset_confirm',
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='accounts/password_reset_complete.html',
        ),
        name='password_reset_complete',
    ),
]