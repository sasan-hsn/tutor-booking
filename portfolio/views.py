from django.shortcuts import render
from .models import TeacherProfile

def landing_page(request):
    teacher = TeacherProfile.objects.prefetch_related('certificates').first()
    context = {
        'teacher': teacher,
        'certificates': teacher.certificates.all() if teacher else [],
        'hero_subtext': teacher.headline if teacher and teacher.headline else "Unlock your English potential with personalized, engaging lessons tailored to your goals."
    }
    return render(request, "home.html", context)
