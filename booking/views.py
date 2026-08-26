from datetime import date, datetime, timedelta

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

from .services import (
    get_availability_windows,
    get_available_start_times,
    get_lesson_type_and_price,
    get_calendar_grid,
    get_calendar_navigation
)



@student_required
def student_dashboard(request):
    now = timezone.localtime()

    upcoming_bookings = (
        Booking.objects
        .filter(
            student=request.user,
            status=Booking.Status.CONFIRMED,
            date__gte=now.date(),
        )
        .exclude(
            date=now.date(),
            start_time__lt=now.time(),
        )
        .select_related('teacher', 'teacher__user')
        .order_by('date', 'start_time')[:10]
    )

    for booking in upcoming_bookings:
        booking.display_name = booking.teacher.user.get_full_name() or booking.teacher.user.username

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

    week_days = [week_start + timedelta(days=i) for i in range(7)]
    teacher = TeacherProfile.objects.first()

    lesson_type, price, duration_minutes = get_lesson_type_and_price(teacher, request.user)

    week_data = []
    for day in week_days:
        start_times = get_available_start_times(teacher, day, duration_minutes)
        day_slots = [
            {
                'start_time': t,
                'end_time': (datetime.combine(day, t) + timedelta(minutes=duration_minutes)).time(),
            }
            for t in start_times
        ]
        week_data.append({'day': day, 'slots': day_slots})

    prev_week_start = week_start - timedelta(days=7)
    can_go_prev = prev_week_start >= today

    context = {
        'week_data': week_data,
        'week_start': week_start,
        'next_week_start': week_start + timedelta(days=7),
        'prev_week_start': prev_week_start,
        'can_go_prev': can_go_prev,
        'today': today,
        'teacher': teacher,
        'lesson_type': lesson_type,
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

    week_days = [week_start + timedelta(days=i) for i in range(7)]
    teacher = TeacherProfile.objects.first()

    lesson_type, price, duration_minutes = get_lesson_type_and_price(teacher, request.user)

    week_data = []
    for day in week_days:
        start_times = get_available_start_times(teacher, day, duration_minutes)
        day_slots = [
            {
                'start_time': t,
                'end_time': (datetime.combine(day, t) + timedelta(minutes=duration_minutes)).time(),
            }
            for t in start_times
        ]
        week_data.append({'day': day, 'slots': day_slots})

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
    date_str = request.POST.get('date')
    start_time_str = request.POST.get('start_time')

    if not date_str or not start_time_str:
        return JsonResponse({'error': 'date and start_time are required.'}, status=400)

    try:
        date_val = date.fromisoformat(date_str)
        start_time_val = datetime.strptime(start_time_str, '%H:%M').time()
    except ValueError:
        return JsonResponse({'error': 'Invalid date or time format.'}, status=400)

    teacher = TeacherProfile.objects.first()

    if teacher.user == request.user:
        return JsonResponse({'error': 'You cannot book your own availability.'}, status=400)

    try:
        with transaction.atomic():
            teacher = TeacherProfile.objects.select_for_update().get(pk=teacher.pk)

            lesson_type, price, duration_minutes = get_lesson_type_and_price(teacher, request.user)
            available_starts = get_available_start_times(teacher, date_val, duration_minutes)

            if start_time_val not in available_starts:
                return JsonResponse({'error': 'This time is no longer available.'}, status=409)

            end_time_val = (
                datetime.combine(date_val, start_time_val) + timedelta(minutes=duration_minutes)
            ).time()

            booking = Booking.objects.create(
                student=request.user,
                teacher=teacher,
                date=date_val,
                start_time=start_time_val,
                end_time=end_time_val,
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
    now = timezone.localtime()

    upcoming_bookings = (
        Booking.objects
        .filter(
            teacher__user=request.user,
            status=Booking.Status.CONFIRMED,
            date__gte=now.date(),
        )
        .exclude(
            date=now.date(),
            start_time__lt=now.time(),
        )
        .select_related('student')
        .order_by('date', 'start_time')[:10]
    )

    for booking in upcoming_bookings:
        booking.display_name = booking.student.get_full_name() or booking.student.username

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
    lesson_requests = Booking.objects.filter(
        Q(status=Booking.Status.PENDING) | Q(cancellation_requested=True),
        teacher__user=request.user,
    ).select_related('student').order_by('date', 'start_time')

    for booking in lesson_requests:
        booking.display_name = booking.student.get_full_name() or booking.student.username
        booking.time_range = f'{booking.start_time:%H:%M}–{booking.end_time:%H:%M}'
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

    now = timezone.localtime()
    today = now.date()
    current_time = now.time()

    for student in students:
        student_bookings = Booking.objects.filter(
            student=student,
            teacher__user=request.user,
        )

        student.completed_count = student_bookings.filter(
            status=Booking.Status.COMPLETED,
        ).count() + student_bookings.filter(
            status=Booking.Status.CONFIRMED,
        ).filter(
            Q(date__lt=today) | Q(date=today, start_time__lt=current_time)
        ).count()

        student.upcoming_count = student_bookings.filter(
            status=Booking.Status.CONFIRMED,
        ).filter(
            Q(date__gt=today) | Q(date=today, start_time__gte=current_time)
        ).count()

        last_lesson = student_bookings.filter(
            status__in=[Booking.Status.COMPLETED, Booking.Status.CONFIRMED],
        ).filter(
            Q(date__lt=today) | Q(date=today, start_time__lt=current_time)
        ).order_by('-date', '-start_time').first()

        student.previous_date = last_lesson.date if last_lesson else None

        next_lesson = student_bookings.filter(
            status=Booking.Status.CONFIRMED,
        ).filter(
            Q(date__gt=today) | Q(date=today, start_time__gte=current_time)
        ).order_by('date', 'start_time').first()

        student.next_date = next_lesson.date if next_lesson else None

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
    today = timezone.localdate()

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
    today = timezone.localdate()

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

    booking.display_name = booking.student.first_name or booking.student.username

    context = {
        "booking": booking,
        "cancellable_statuses": [Booking.Status.CONFIRMED],
        "date_time_label": f"{booking.date.strftime('%a, %b')} {booking.date.day}, {booking.date.year} · {booking.start_time.strftime('%H:%M')}–{booking.end_time.strftime('%H:%M')}",    }

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

    booking.status = Booking.Status.CANCELLED
    booking.save()

    return JsonResponse({"success": True})



@student_required
def student_calendar(request):
    today = timezone.localdate()
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
    today = timezone.localdate()
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

    booking.display_name = booking.teacher.user.first_name or booking.teacher.user.username

    can_request_cancellation = (
        booking.status == Booking.Status.CONFIRMED
        and not booking.cancellation_requested
    )

    context = {
        "booking": booking,
        "cancellable_statuses": [],
        "can_request_cancellation": can_request_cancellation,
        "date_time_label": f"{booking.date.strftime('%a, %b')} {booking.date.day}, {booking.date.year} · {booking.start_time.strftime('%H:%M')}–{booking.end_time.strftime('%H:%M')}",
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



