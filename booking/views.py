from datetime import date, timedelta
from django.utils import timezone
from django.shortcuts import render
from accounts.decorators import student_required
from django.template.loader import render_to_string
from django.http import JsonResponse

from booking.models import Booking, AvailabilitySlot
from portfolio.models import TeacherProfile



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



@student_required
def student_booking(request):
    today = timezone.localdate()

    week_start_param = request.GET.get('week_start')
    if week_start_param:
        try:
            week_start = date.fromisoformat(week_start_param)
        except ValueError:
            week_start = today
    else:
        week_start = today

    if week_start < today:
        week_start = today

    week_end = week_start + timedelta(days=6)
    week_days = [week_start + timedelta(days=i) for i in range(7)]

    teacher = TeacherProfile.objects.first()

    slots = (
        AvailabilitySlot.objects
        .filter(teacher=teacher, date__range=(week_start, week_end))
        .order_by('date', 'start_time')
    )

    slots_by_day = {day: [] for day in week_days}
    for slot in slots:
        slots_by_day[slot.date].append(slot)

    week_data = [
        {'day': day, 'slots': slots_by_day[day]}
        for day in week_days
    ]

    prev_week_start = week_start - timedelta(days=7)
    can_go_prev = prev_week_start >= today

    context = {
        'week_data': week_data,
        'slots_by_day': slots_by_day,
        'week_start': week_start,
        'next_week_start': week_start + timedelta(days=7),
        'prev_week_start': prev_week_start,
        'can_go_prev': can_go_prev,
        'today': today,
    }
    return render(request, 'student_booking.html', context)



@student_required
def student_booking_week_ajax(request):
    today = timezone.localdate()
    week_start_param = request.GET.get('week_start')

    try:
        week_start = date.fromisoformat(week_start_param)
    except (TypeError, ValueError):
        week_start = today

    if week_start < today:
        week_start = today

    week_end = week_start + timedelta(days=6)
    week_days = [week_start + timedelta(days=i) for i in range(7)]

    teacher = TeacherProfile.objects.first()
    slots = (
        AvailabilitySlot.objects
        .filter(teacher=teacher, date__range=(week_start, week_end))
        .order_by('date', 'start_time')
    )
    slots_by_day = {day: [] for day in week_days}
    for slot in slots:
        slots_by_day[slot.date].append(slot)
    week_data = [{'day': day, 'slots': slots_by_day[day]} for day in week_days]

    prev_week_start = week_start - timedelta(days=7)
    can_go_prev = prev_week_start >= today

    html = render_to_string('partials/_day_picker_columns.html', {'week_data': week_data}, request=request)

    return JsonResponse({
        'html': html,
        'week_label': f"{week_start.strftime('%b %d')} – {week_days[-1].strftime('%b %d, %Y')}",
        'prev_week_start': prev_week_start.isoformat(),
        'next_week_start': (week_start + timedelta(days=7)).isoformat(),
        'can_go_prev': can_go_prev,
    })