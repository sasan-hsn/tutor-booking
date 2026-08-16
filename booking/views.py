from datetime import date, timedelta
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import ValidationError
from django.shortcuts import render
from accounts.decorators import student_required, teacher_required
from django.template.loader import render_to_string
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404

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

    for booking in upcoming_bookings:
        booking.display_name = booking.slot.teacher.user.get_full_name() or booking.slot.teacher.user.username

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


@student_required
@require_POST
def book_slot(request):
    slot_id = request.POST.get('slot_id')
    if not slot_id:
        return JsonResponse({'error': 'slot_id is required.'}, status=400)

    try:
        with transaction.atomic():
            slot = AvailabilitySlot.objects.select_for_update().get(pk=slot_id)

            if slot.is_booked:
                return JsonResponse({'error': 'This slot is already booked.'}, status=409)

            if slot.teacher.user == request.user:
                return JsonResponse({'error': 'You cannot book your own slot.'}, status=400)

            has_previous_lesson = Booking.objects.filter(
                student=request.user,
                slot__teacher=slot.teacher,
                status__in=[Booking.Status.CONFIRMED, Booking.Status.COMPLETED],
            ).exists()

            if slot.teacher.offers_trial and not has_previous_lesson:
                lesson_type = Booking.LessonType.TRIAL
                price = slot.teacher.trial_price 
            else:
                lesson_type = Booking.LessonType.REGULAR
                price = slot.teacher.lesson_price

            booking = Booking.objects.create(
                student=request.user,
                slot=slot,
                lesson_type=lesson_type,
                price=price,
            )

    except AvailabilitySlot.DoesNotExist:
        return JsonResponse({'error': 'Slot not found.'}, status=404)
    except ValidationError as e:
        return JsonResponse({'error': str(e)}, status=400)

    return JsonResponse({
        'success': True,
        'booking_id': booking.id,
        'lesson_type': booking.get_lesson_type_display(),
        'price': str(booking.price) if booking.price is not None else 'Free',
    })



@teacher_required
def teacher_dashboard(request):
    now = timezone.localtime()

    upcoming_bookings = (
        Booking.objects
        .filter(
            slot__teacher__user = request.user,
            status = Booking.Status.CONFIRMED,
            slot__date__gte=now.date(),
        )
        .exclude(
            slot__date=now.date(),
            slot__start_time__lt=now.time(),
        )
        .select_related('slot', 'student')
        .order_by('slot__date', 'slot__start_time')[:10]
    )

    for booking in upcoming_bookings:
        booking.display_name = booking.student.get_full_name() or booking.student.username


    lesson_requests_count = Booking.objects.filter(
        slot__teacher__user = request.user,
        status=Booking.Status.PENDING,
    ).count()

    return render(request, 'teacher_dashboard.html', {
        'upcoming_bookings': upcoming_bookings,
        'lesson_requests_count' : lesson_requests_count
    })


@teacher_required
def teacher_lesson_requests(request):

    lesson_requests = Booking.objects.filter(
        slot__teacher__user = request.user,
        status = Booking.Status.PENDING,
    ).select_related('slot', 'student').order_by('slot__date', 'slot__start_time')

    for booking in lesson_requests:
        booking.display_name = booking.student.get_full_name() or booking.student.username
        booking.time_range = f'{booking.slot.start_time:%H:%M}–{booking.slot.end_time:%H:%M}'

    return render(request, 'partials/_lesson_requests_list.html', {
    'lesson_requests': lesson_requests,
    })


@teacher_required
@require_POST
def respond_to_booking(request, booking_id):
    booking = get_object_or_404(
        Booking,
        id=booking_id,
        slot__teacher__user=request.user,
        status=Booking.Status.PENDING,
    )

    action = request.POST.get('action')

    if action == 'accept':
        booking.status = Booking.Status.CONFIRMED
    elif action == 'decline':
        booking.status = Booking.Status.CANCELLED
    else:
        return JsonResponse({'error': 'Invalid action.'}, status=400)

    booking.save()

    return JsonResponse({'success': True})