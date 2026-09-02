from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import student_required, teacher_required
from booking.models import Booking, RegularAvailability, WeeklyOverride
from portfolio.models import TeacherProfile
from accounts.models import StudentProfile
from accounts.avatar_utils import get_avatar_color


from .services import (
    get_availability_windows,
    get_available_start_times,
    get_lesson_type_and_price,
    get_calendar_grid,
    get_calendar_navigation,
    get_week_data,
)



@student_required
def student_dashboard(request):
    now = timezone.localtime()

    upcoming_bookings = (
        Booking.objects
        .filter(
            student=request.user,
            status=Booking.Status.CONFIRMED,
            start_at__gte=timezone.now(),
        )
        .select_related('teacher', 'teacher__user')
        .order_by('start_at')[:10]
    )

    return render(request, 'student_dashboard.html', {
        'upcoming_bookings': upcoming_bookings,
    })



@student_required
def student_booking(request):
    student_tz = ZoneInfo(request.user.timezone)
    today = timezone.localtime(timezone.now(), student_tz).date()

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

    week_days = [week_start + timedelta(days=i) for i in range(7)]
    teacher = TeacherProfile.objects.first()

    lesson_type, price, duration_minutes = get_lesson_type_and_price(teacher, request.user)

    week_data = get_week_data(teacher, student_tz, week_days, duration_minutes)

    prev_week_start = week_start - timedelta(days=7)
    can_go_prev = prev_week_start >= today

    teacher_profile_picture = None
    if teacher.profile_picture:
        teacher_profile_picture = teacher.profile_picture.url

    context = {
        'week_data': week_data,
        'week_start': week_start,
        'next_week_start': week_start + timedelta(days=7),
        'prev_week_start': prev_week_start,
        'can_go_prev': can_go_prev,
        'today': today,
        'teacher': teacher,
        'lesson_type': lesson_type,
        'lesson_type_display': dict(Booking.LessonType.choices)[lesson_type],
        'teacher_avatar_color': get_avatar_color(teacher.user.id),
        'teacher_profile_picture': teacher_profile_picture,
    }
    return render(request, 'student_booking.html', context)



@student_required
def student_booking_week_ajax(request):
    student_tz = ZoneInfo(request.user.timezone)
    today = timezone.localtime(timezone.now(), student_tz).date()

    week_start_param = request.GET.get('week_start')
    try:
        week_start = date.fromisoformat(week_start_param)
    except (TypeError, ValueError):
        week_start = today

    if week_start < today:
        week_start = today

    week_days = [week_start + timedelta(days=i) for i in range(7)]
    teacher = TeacherProfile.objects.first()

    lesson_type, price, duration_minutes = get_lesson_type_and_price(teacher, request.user)

    week_data = get_week_data(teacher, student_tz, week_days, duration_minutes)

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
    start_at_str = request.POST.get('start_at')

    if not start_at_str:
        return JsonResponse({'error': 'start_at is required.'}, status=400)

    try:
        start_at_val = datetime.fromisoformat(start_at_str)
        if timezone.is_naive(start_at_val):
            return JsonResponse({'error': 'Invalid start_at format.'}, status=400)
    except ValueError:
        return JsonResponse({'error': 'Invalid start_at format.'}, status=400)

    teacher = TeacherProfile.objects.first()

    if teacher.user == request.user:
        return JsonResponse({'error': 'You cannot book your own availability.'}, status=400)

    try:
        with transaction.atomic():
            teacher = TeacherProfile.objects.select_for_update().get(pk=teacher.pk)

            lesson_type, price, duration_minutes = get_lesson_type_and_price(teacher, request.user)

            # Re-derive available starts for the teacher's local day(s)
            # that could contain this instant, and confirm it's still open.
            teacher_tz = ZoneInfo(teacher.user.timezone)
            teacher_local_date = timezone.localtime(start_at_val, teacher_tz).date()
            available_starts = get_available_start_times(teacher, teacher_local_date, duration_minutes)

            if start_at_val not in available_starts:
                return JsonResponse({'error': 'This time is no longer available.'}, status=409)

            end_at_val = start_at_val + timedelta(minutes=duration_minutes)

            booking = Booking.objects.create(
                student=request.user,
                teacher=teacher,
                start_at=start_at_val,
                end_at=end_at_val,
                lesson_type=lesson_type,
                price=price,
            )

    except IntegrityError:
        return JsonResponse({'error': 'This time is no longer available.'}, status=409)
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
    upcoming_bookings = (
        Booking.objects
        .filter(
            teacher__user=request.user,
            status=Booking.Status.CONFIRMED,
        )
        .select_related('student')
        .order_by('start_at')[:10]
    )

    lesson_requests_count = Booking.objects.filter(
        Q(status=Booking.Status.PENDING) | Q(cancellation_requested=True),
        teacher__user=request.user,
    ).count()

    return render(request, 'teacher_dashboard.html', {
        'upcoming_bookings': upcoming_bookings,
        'lesson_requests_count': lesson_requests_count
    })


@teacher_required
def teacher_lesson_requests(request):
    viewer_tz = ZoneInfo(request.user.timezone)

    lesson_requests = Booking.objects.filter(
        Q(status=Booking.Status.PENDING) | Q(cancellation_requested=True),
        teacher__user=request.user,
    ).select_related('student').order_by('start_at')

    for booking in lesson_requests:
        local_start = timezone.localtime(booking.start_at, viewer_tz)
        local_end = timezone.localtime(booking.end_at, viewer_tz)
        booking.local_date = local_start.date()
        booking.time_range = f'{local_start:%H:%M}–{local_end:%H:%M}'
        booking.is_cancellation = booking.cancellation_requested

    return render(request, 'partials/_lesson_requests_list.html', {
        'lesson_requests': lesson_requests,
    })


@teacher_required
@require_POST
def respond_to_booking(request, booking_id):
    booking = get_object_or_404(
        Booking,
        id=booking_id,
        teacher__user=request.user,
    )

    action = request.POST.get('action')
    if action not in ('accept', 'decline'):
        return JsonResponse({'error': 'Invalid action.'}, status=400)

    if booking.cancellation_requested:
        if action == 'accept':
            booking.status = Booking.Status.CANCELLED
        booking.cancellation_requested = False
        booking.save()
    elif booking.status == Booking.Status.PENDING:
        booking.status = Booking.Status.CONFIRMED if action == 'accept' else Booking.Status.CANCELLED
        booking.save()
    else:
        return JsonResponse({'error': 'This booking is not awaiting a response.'}, status=400)

    return JsonResponse({'success': True})



@teacher_required
def student_management(request):
    User = get_user_model()

    students = User.objects.filter(
        bookings__teacher__user=request.user,
        bookings__status__in=[Booking.Status.CONFIRMED, Booking.Status.COMPLETED],
    ).distinct()

    now = timezone.now()

    for student in students:
        student_bookings = Booking.objects.filter(
            student=student,
            teacher__user=request.user,
        )

        student.completed_count = student_bookings.filter(
            status=Booking.Status.COMPLETED,
        ).count() + student_bookings.filter(
            status=Booking.Status.CONFIRMED,
            start_at__lt=now,
        ).count()

        student.upcoming_count = student_bookings.filter(
            status=Booking.Status.CONFIRMED,
            start_at__gte=now,
        ).count()

        last_lesson = student_bookings.filter(
            status__in=[Booking.Status.COMPLETED, Booking.Status.CONFIRMED],
            start_at__lt=now,
        ).order_by('-start_at').first()

        student.previous_booking = last_lesson

        next_lesson = student_bookings.filter(
            status=Booking.Status.CONFIRMED,
            start_at__gte=now,
        ).order_by('start_at').first()

        student.next_booking = next_lesson

        student.display_name = student.get_full_name() or student.username

    return render(request, 'teacher_student_management.html', {
        'students': students,
    })




@teacher_required
def teacher_regular_schedule(request):
    availabilities = RegularAvailability.objects.filter(
        teacher=request.user.teacher_profile,
    ).order_by('day_of_week', 'start_time')

    schedule = {str(day): [] for day in range(7)}

    for availability in availabilities:
        schedule[str(availability.day_of_week)].append({
            'id': availability.id,
            'start': availability.start_time.strftime('%H:%M'),
            'end': availability.end_time.strftime('%H:%M'),
        })

    return JsonResponse(schedule)



@teacher_required
@require_POST
def teacher_regular_schedule_add(request):
    day_of_week = request.POST.get('day_of_week')
    start_time = request.POST.get('start_time')
    end_time = request.POST.get('end_time')

    availability = RegularAvailability(
        teacher=request.user.teacher_profile,
        day_of_week=day_of_week,
        start_time=start_time,
        end_time=end_time,
    )

    try:
        availability.full_clean()
        availability.save()
    except ValidationError as e:
        return JsonResponse({'error': e.message_dict}, status=400)

    return JsonResponse({
        'id': availability.id,
        'day_of_week': availability.day_of_week,
        'start': availability.start_time.strftime('%H:%M'),
        'end': availability.end_time.strftime('%H:%M'),
    })


@teacher_required
@require_POST
def teacher_regular_schedule_delete(request, availability_id):
    availability = get_object_or_404(
        RegularAvailability,
        id=availability_id,
        teacher=request.user.teacher_profile,
    )
    availability.delete()
    return JsonResponse({'success': True})



@teacher_required
def teacher_weekly_override(request):
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    week_days = [week_start + timedelta(days=i) for i in range(7)]

    teacher_profile = request.user.teacher_profile

    regular_availabilities = RegularAvailability.objects.filter(teacher=teacher_profile)
    regular_by_day = {}
    for reg in regular_availabilities:
        regular_by_day.setdefault(reg.day_of_week, []).append({
            'start': reg.start_time.strftime('%H:%M'),
            'end': reg.end_time.strftime('%H:%M'),
        })

    overrides = WeeklyOverride.objects.filter(
        teacher=teacher_profile,
        date__range=(week_start, week_start + timedelta(days=6)),
        is_available=True,
    ).order_by('date', 'start_time')

    schedule = {}
    for day in week_days:
        schedule[day.isoformat()] = {
            'is_past': day < today,
            'regular_ranges': regular_by_day.get(day.weekday(), []),
            'override_ranges': [],
        }

    for override in overrides:
        schedule[override.date.isoformat()]['override_ranges'].append({
            'id': override.id,
            'start': override.start_time.strftime('%H:%M'),
            'end': override.end_time.strftime('%H:%M'),
        })

    return JsonResponse(schedule)



@teacher_required
@require_POST
def teacher_weekly_override_add(request):
    date_str = request.POST.get('date')
    start_time = request.POST.get('start_time')
    end_time = request.POST.get('end_time')

    override = WeeklyOverride(
        teacher=request.user.teacher_profile,
        date=date_str,
        start_time=start_time,
        end_time=end_time,
        is_available=True,
    )

    try:
        override.full_clean()
        override.save()
    except ValidationError as e:
        return JsonResponse({'error': e.message_dict}, status=400)

    return JsonResponse({
        'id': override.id,
        'date': override.date.isoformat(),
        'start': override.start_time.strftime('%H:%M'),
        'end': override.end_time.strftime('%H:%M'),
    })



@teacher_required
@require_POST
def teacher_weekly_override_delete(request, override_id):
    override = get_object_or_404(
        WeeklyOverride,
        id=override_id,
        teacher=request.user.teacher_profile,
    )
    override.delete()
    return JsonResponse({'success': True})



@teacher_required
def teacher_calendar(request):
    teacher = get_object_or_404(TeacherProfile, user=request.user)
    today = timezone.localtime(timezone.now(), ZoneInfo(request.user.timezone)).date()

    nav = get_calendar_navigation(request, today)

    calendar_grid = get_calendar_grid(
        teacher=teacher,
        year=nav["current_year"],
        month=nav["current_month"],
    )

    students = (
        StudentProfile.objects.filter(user__bookings__teacher=teacher)
        .select_related("user")
        .distinct()
    )

    context = {
        "teacher": teacher,
        "calendar_grid": calendar_grid,
        "students": students,
        "ajax_url_name": "booking:teacher_calendar_ajax",
        **nav,
    }

    return render(request, "calendar.html", context)


@teacher_required
def teacher_calendar_ajax(request):
    teacher = get_object_or_404(TeacherProfile, user=request.user)
    today = timezone.localtime(timezone.now(), ZoneInfo(request.user.timezone)).date()

    nav = get_calendar_navigation(request, today)

    calendar_grid = get_calendar_grid(
        teacher=teacher,
        year=nav["current_year"],
        month=nav["current_month"],
    )

    context = {
        "calendar_grid": calendar_grid,
        "ajax_url_name": "booking:teacher_calendar_ajax",
        **nav,
    }

    return render(request, "partials/_calendar_grid.html", context)



@teacher_required
def lesson_detail(request, booking_id):
    teacher = get_object_or_404(TeacherProfile, user=request.user)
    booking = get_object_or_404(
        Booking.objects.select_related("student"),
        pk=booking_id,
        teacher=teacher,
    )

    viewer_tz = ZoneInfo(request.user.timezone)
    local_start = timezone.localtime(booking.start_at, viewer_tz)
    local_end = timezone.localtime(booking.end_at, viewer_tz)

    context = {
        "booking": booking,
        "cancellable_statuses": [Booking.Status.CONFIRMED],
        "date_time_label": f"{local_start.strftime('%a, %b')} {local_start.day}, {local_start.year} · {local_start.strftime('%H:%M')}–{local_end.strftime('%H:%M')}",
    }

    return render(request, "partials/_lesson_detail_modal.html", context)


@teacher_required
@require_POST
def cancel_lesson(request, booking_id):
    teacher = get_object_or_404(TeacherProfile, user=request.user)
    booking = get_object_or_404(
        Booking,
        pk=booking_id,
        teacher=teacher,
        status__in=[Booking.Status.PENDING, Booking.Status.CONFIRMED],
    )

    if booking.is_awaiting_resolution:
        return JsonResponse({"error": "This lesson has already passed and needs resolution, not cancellation."}, status=400)

    booking.status = Booking.Status.CANCELLED
    booking.save()

    return JsonResponse({"success": True})


@teacher_required
@require_POST
def complete_lesson(request, booking_id):
    teacher = get_object_or_404(TeacherProfile, user=request.user)
    booking = get_object_or_404(
        Booking,
        pk=booking_id,
        teacher=teacher,
        status=Booking.Status.CONFIRMED,
    )

    if not booking.is_awaiting_resolution:
        return JsonResponse({"error": "This lesson hasn't ended yet."}, status=400)

    booking.status = Booking.Status.COMPLETED
    booking.completion_note = request.POST.get('note', '').strip()
    booking.save()

    return JsonResponse({"success": True})


@teacher_required
@require_POST
def mark_lesson_not_held(request, booking_id):
    teacher = get_object_or_404(TeacherProfile, user=request.user)
    booking = get_object_or_404(
        Booking,
        pk=booking_id,
        teacher=teacher,
        status=Booking.Status.CONFIRMED,
    )

    if not booking.is_awaiting_resolution:
        return JsonResponse({"error": "This lesson hasn't ended yet."}, status=400)

    booking.status = Booking.Status.CANCELLED
    booking.save()

    return JsonResponse({"success": True})



@student_required
def student_calendar(request):
    today = timezone.localtime(timezone.now(), ZoneInfo(request.user.timezone)).date()
    nav = get_calendar_navigation(request, today)

    calendar_grid = get_calendar_grid(
        student=request.user,
        year=nav["current_year"],
        month=nav["current_month"],
    )

    context = {
        "calendar_grid": calendar_grid,
        "ajax_url_name": "booking:student_calendar_ajax",
        **nav,
    }

    return render(request, "student_calendar.html", context)


@student_required
def student_calendar_ajax(request):
    today = timezone.localtime(timezone.now(), ZoneInfo(request.user.timezone)).date()
    nav = get_calendar_navigation(request, today)

    calendar_grid = get_calendar_grid(
        student=request.user,
        year=nav["current_year"],
        month=nav["current_month"],
    )

    context = {
        "calendar_grid": calendar_grid,
        "ajax_url_name": "booking:student_calendar_ajax",
        **nav,
    }

    return render(request, "partials/_calendar_grid.html", context)


@student_required
def lesson_detail_student(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_related("teacher__user"),
        pk=booking_id,
        student=request.user,
    )

    viewer_tz = ZoneInfo(request.user.timezone)
    local_start = timezone.localtime(booking.start_at, viewer_tz)
    local_end = timezone.localtime(booking.end_at, viewer_tz)

    can_request_cancellation = (
        booking.status == Booking.Status.CONFIRMED
        and not booking.cancellation_requested
    )

    context = {
        "booking": booking,
        "cancellable_statuses": [],
        "can_request_cancellation": can_request_cancellation,
        "date_time_label": f"{local_start.strftime('%a, %b')} {local_start.day}, {local_start.year} · {local_start.strftime('%H:%M')}–{local_end.strftime('%H:%M')}",
    }

    return render(request, "partials/_lesson_detail_modal.html", context)



@student_required
@require_POST
def request_cancellation(request, booking_id):
    booking = get_object_or_404(
        Booking,
        pk=booking_id,
        student=request.user,
        status__in=[Booking.Status.PENDING, Booking.Status.CONFIRMED],
        cancellation_requested=False,
    )

    booking.cancellation_requested = True
    booking.save()

    return JsonResponse({"success": True})