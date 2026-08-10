from django.utils import timezone
from django.shortcuts import render
from accounts.decorators import student_required
from booking.models import Booking


@student_required
def student_dashboard(request):
    now = timezone.localtime()

    upcoming_bookings = (
        Booking.objects
        .filter(
            student=request.user,
            status=Booking.Status.CONFIRMED,
            slot__date__gte=now.date(),
        )
        .exclude(
            slot__date=now.date(),
            slot__start_time__lt=now.time(),
        )
        .select_related('slot', 'slot__teacher', 'slot__teacher__user')
        .order_by('slot__date', 'slot__start_time')[:10]
    )

    return render(request, 'student_dashboard.html', {
        'upcoming_bookings': upcoming_bookings,
    })