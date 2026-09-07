from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import teacher_required
from booking.models import Review

from .forms import (
    CertificateForm,
    TeacherAccountSettingsForm,
    TeacherBookingSettingsForm,
    TeacherPortfolioSettingsForm,
)
from .models import Certificate, TeacherProfile


def landing_page(request):
    teacher = (
        TeacherProfile.objects.select_related('user')
        .prefetch_related('certificates')
        .first()
    )
    certificates = teacher.certificates.all() if teacher else []
    hero_subtext = (
        teacher.headline
        if teacher and teacher.headline
        else "Unlock your English potential with personalized, engaging lessons tailored to your goals."
    )
    reviews = (
        Review.objects.filter(
            is_approved=True,
            booking__teacher=teacher,
        )
        .exclude(comment__isnull=True)
        .exclude(comment__exact='')
        .select_related('student')[:6]
        if teacher
        else Review.objects.none()
    )

    context = {
        'teacher': teacher,
        'certificates': certificates,
        'hero_subtext': hero_subtext,
        'reviews': reviews,
    }
    return render(request, "portfolio/home.html", context)


@teacher_required
def teacher_settings_account(request):
    teacher = request.user.teacher_profile
    if request.method == 'POST':
        form = TeacherAccountSettingsForm(request.POST, request.FILES, user=request.user, profile=teacher)
        if form.is_valid():
            form.save()
            messages.success(request, 'Account settings updated.')
            return redirect('portfolio:teacher_settings_account')
    else:
        form = TeacherAccountSettingsForm(user=request.user, profile=teacher)
    return render(request, 'portfolio/teacher_settings_account.html', {'form': form, 'active': 'account'})


@teacher_required
def teacher_settings_portfolio(request):
    teacher = request.user.teacher_profile
    if request.method == 'POST':
        form = TeacherPortfolioSettingsForm(request.POST, request.FILES, instance=teacher)
        if form.is_valid():
            form.save()
            messages.success(request, 'Portfolio updated.')
            return redirect('portfolio:teacher_settings_portfolio')
    else:
        form = TeacherPortfolioSettingsForm(instance=teacher)

    certificates = teacher.certificates.all()
    cert_form = CertificateForm()

    return render(request, 'portfolio/teacher_settings_portfolio.html', {
        'form': form,
        'certificates': certificates,
        'cert_form': cert_form,
        'active': 'portfolio',
    })


@teacher_required
@require_POST
def teacher_certificate_add(request):
    teacher = request.user.teacher_profile
    form = CertificateForm(request.POST)
    if form.is_valid():
        cert = form.save(commit=False)
        cert.teacher = teacher
        cert.save()
        return JsonResponse({
            'id': cert.id,
            'title': cert.title,
            'issued_by': cert.issued_by,
            'issue_date': cert.issue_date.isoformat() if cert.issue_date else '',
        })
    first_error = next(iter(form.errors.values()))[0]
    return JsonResponse({'error': first_error}, status=400)


@teacher_required
@require_POST
def teacher_certificate_delete(request, certificate_id):
    cert = get_object_or_404(Certificate, id=certificate_id, teacher=request.user.teacher_profile)
    cert.delete()
    return JsonResponse({'success': True})


@teacher_required
def teacher_settings_booking(request):
    teacher = request.user.teacher_profile
    if request.method == 'POST':
        form = TeacherBookingSettingsForm(request.POST, instance=teacher)
        if form.is_valid():
            form.save()
            messages.success(request, 'Booking settings updated.')
            return redirect('portfolio:teacher_settings_booking')
    else:
        form = TeacherBookingSettingsForm(instance=teacher)

    return render(request, 'portfolio/teacher_settings_booking.html', {
        'form': form,
        'active': 'booking',
    })