from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import (
    StudentProfileSettingsForm,
    StudentSignUpForm,
    StyledAuthenticationForm,
    TeacherSignUpForm,
)
from .models import User
from .rate_limiting import (
    check_resend_rate_limit,
    get_client_ip,
    record_resend_attempt,
)
from .tasks import safe_send_verification_email
from .tokens import email_verification_token_generator


def student_signup(request):
    next_url = request.POST.get('next') or request.GET.get('next')
    safe_next = next_url if (next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()})) else None

    if request.method == 'POST':
        form = StudentSignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            transaction.on_commit(lambda: safe_send_verification_email(user.pk, next_url=safe_next))
            if safe_next:
                return redirect(safe_next)
            return redirect('booking:student_dashboard')
    else:
        form = StudentSignUpForm()
    return render(request, 'accounts/student_signup.html', {'form': form, 'next': next_url or ''})


def user_login(request):
    next_url = request.POST.get('next') or request.GET.get('next')

    if request.method == 'POST':
        form = StyledAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)

            if user.role == User.Role.TEACHER:
                return redirect('booking:teacher_dashboard')
            return redirect('booking:student_dashboard')
    else:
        form = StyledAuthenticationForm()
    return render(request, 'accounts/login.html', {'form': form, 'next': next_url or ''})


@require_POST
def user_logout(request):
    next_url = request.POST.get('next') or request.GET.get('next')
    logout(request)
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect('/')


@login_required
def profile_settings(request):
    if request.user.role == User.Role.TEACHER:
        return redirect('portfolio:teacher_settings_account')

    profile = getattr(request.user, 'student_profile', None)

    if request.method == 'POST':
        form = StudentProfileSettingsForm(request.POST, request.FILES, profile=profile, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated.')
            return redirect('accounts:profile_settings')
    else:
        form = StudentProfileSettingsForm(profile=profile, user=request.user)

    return render(request, 'accounts/profile_settings.html', {
        'form': form,
    })


def teacher_signup(request):
    next_url = request.POST.get('next') or request.GET.get('next')
    safe_next = next_url if (next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()})) else None

    if request.method == 'POST':
        form = TeacherSignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            transaction.on_commit(lambda: safe_send_verification_email(user.pk, next_url=safe_next))
            if safe_next:
                return redirect(safe_next)
            return redirect('booking:teacher_dashboard')
    else:
        form = TeacherSignUpForm()
    return render(request, 'accounts/teacher_signup.html', {'form': form, 'next': next_url or ''})


def verify_email(request, token):
    """
    Handle email verification link clicks:
    - If valid and logged out: auto-login, mark verified, redirect to next or dashboard.
    - If valid and logged in as target user: mark verified, redirect to next or dashboard.
    - If valid and logged in as different user: display conflict interception screen without mutating session.
    - If expired or invalid: display clear error screen with resend option.
    """
    next_url = request.POST.get('next') or request.GET.get('next')
    safe_next = next_url if (next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()})) else None

    user, status = email_verification_token_generator.check_token(token)

    if status == 'expired':
        return render(request, 'accounts/verify_email_invalid.html', {
            'status': 'expired',
            'target_user': user,
            'next_url': safe_next,
        }, status=400)

    if status == 'invalid' or not user:
        return render(request, 'accounts/verify_email_invalid.html', {
            'status': 'invalid',
        }, status=400)

    # Valid token: cross-session conflict protection
    if request.user.is_authenticated and request.user.pk != user.pk:
        return render(request, 'accounts/verify_email_conflict.html', {
            'logged_in_user': request.user,
            'target_user': user,
            'token': token,
            'next_url': safe_next,
        }, status=200)

    # If logged out, auto-login user
    if not request.user.is_authenticated:
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')

    if not user.is_email_verified:
        user.is_email_verified = True
        user.save(update_fields=['is_email_verified'])
        messages.success(request, 'Your email has been successfully verified.')
    else:
        messages.info(request, 'Your email is already verified.')

    if safe_next:
        return redirect(safe_next)

    if user.role == User.Role.TEACHER:
        return redirect('booking:teacher_dashboard')
    return redirect('booking:student_dashboard')


@require_POST
def resend_verification_email(request):
    """
    Rate-limited endpoint to resend email verification link.
    Enforces a 60-second cooldown and a maximum of 5 requests per hour per user/IP.
    Supports both JSON/AJAX and standard browser form POST.
    """
    client_ip = get_client_ip(request)
    email = request.POST.get('email', '').strip().lower()
    next_url = request.POST.get('next') or request.GET.get('next')
    safe_next = next_url if (next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()})) else None

    is_json = (
        request.headers.get('x-requested-with') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
        or request.content_type == 'application/json'
    )

    if request.user.is_authenticated:
        target_user = request.user
    elif email:
        target_user = User.objects.filter(email=email).first()
    else:
        target_user = None

    # Evaluate rate limits
    allowed, reason, retry_after = check_resend_rate_limit(
        user=target_user,
        email=email or (target_user.email if target_user else None),
        ip=client_ip,
    )

    if not allowed:
        if reason == 'cooldown':
            msg = f"Please wait {retry_after} second{'s' if retry_after != 1 else ''} before requesting another verification email."
        else:
            msg = "Too many verification requests. Please try again later."

        if is_json:
            return JsonResponse({'error': msg, 'reason': reason, 'retry_after': retry_after}, status=429)
        messages.warning(request, msg)
        referer = request.META.get('HTTP_REFERER')
        if referer and url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}):
            return redirect(referer)
        return redirect('accounts:login')

    # If target user exists and already verified
    if target_user and target_user.is_email_verified:
        msg = "Your email address is already verified."
        if is_json:
            return JsonResponse({'message': msg, 'already_verified': True}, status=200)
        messages.info(request, msg)
        referer = request.META.get('HTTP_REFERER')
        if referer and url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}):
            return redirect(referer)
        if target_user.role == User.Role.TEACHER:
            return redirect('booking:teacher_dashboard')
        return redirect('booking:student_dashboard')

    # Record rate limit attempt
    record_resend_attempt(
        user=target_user,
        email=email or (target_user.email if target_user else None),
        ip=client_ip,
    )

    # Send verification email if user exists and unverified
    if target_user and not target_user.is_email_verified and target_user.email:
        safe_send_verification_email(target_user.pk, next_url=safe_next)

    msg = "A new verification email has been sent. Please check your inbox."
    if is_json:
        return JsonResponse({'status': 'ok', 'message': msg}, status=200)
    messages.success(request, msg)
    referer = request.META.get('HTTP_REFERER')
    if referer and url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}):
        return redirect(referer)
    if request.user.is_authenticated:
        if request.user.role == User.Role.TEACHER:
            return redirect('booking:teacher_dashboard')
        return redirect('booking:student_dashboard')
    return redirect('accounts:login')
