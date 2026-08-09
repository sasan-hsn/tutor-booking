from django.shortcuts import render
from .models import TeacherProfile
from booking.models import Review

def landing_page(request):
    teacher = TeacherProfile.objects.prefetch_related('certificates').first()
    certificates = teacher.certificates.all() if teacher else []
    hero_subtext = teacher.headline if teacher and teacher.headline else "Unlock your English potential with personalized, engaging lessons tailored to your goals."
    reviews = Review.objects.filter(
        is_approved=True, 
        booking__slot__teacher=teacher).select_related('student')[:6] if teacher else Review.objects.none()
    
    for review in reviews:
        review.star_range = [True] * review.rating + [False] * (5 - review.rating)

    context = {
        'teacher': teacher,
        'certificates': certificates,
        'hero_subtext': hero_subtext,
        'reviews': reviews,
    }
    return render(request, "home.html", context)
