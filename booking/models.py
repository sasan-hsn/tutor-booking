from datetime import datetime
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction


class RegularAvailability(models.Model):
    class DayOfWeek(models.IntegerChoices):
        MONDAY = 0, 'Monday'
        TUESDAY = 1, 'Tuesday'
        WEDNESDAY = 2, 'Wednesday'
        THURSDAY = 3, 'Thursday'
        FRIDAY = 4, 'Friday'
        SATURDAY = 5, 'Saturday'
        SUNDAY = 6, 'Sunday'

    teacher = models.ForeignKey('portfolio.TeacherProfile', on_delete=models.CASCADE,related_name='regular_availabilities')
    day_of_week = models.PositiveSmallIntegerField(choices=DayOfWeek.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ['day_of_week', 'start_time']


    def clean(self):
        super().clean()

        #Validation for logical start and end times
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError({
                'end_time': 'End time must be after start time.'
            })

        #Check for overlapping slots on the same day
        overlapping = RegularAvailability.objects.filter(
            teacher=self.teacher,
            day_of_week=self.day_of_week,
            start_time__lt=self.end_time,
            end_time__gt=self.start_time,
        )

        if self.pk:
            overlapping = overlapping.exclude(pk=self.pk)

        if overlapping.exists():
            raise ValidationError("This time slot overlaps with an existing availability.")    

    def __str__(self):
        return f"{self.get_day_of_week_display()}: {self.start_time} - {self.end_time}"



class WeeklyOverride(models.Model):
    teacher = models.ForeignKey(
        'portfolio.TeacherProfile',
        on_delete=models.CASCADE,
        related_name='weekly_overrides'
    )
    date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    is_available = models.BooleanField(default=True)

    class Meta:
        ordering = ['date', 'start_time']

    def clean(self):
        super().clean()

        # Case 1: Full-day off override (is_available = False)
        if not self.is_available:
            if self.start_time or self.end_time:
                raise ValidationError(
                    'Start and end times must be empty when marking a day as'
                    ' full-day off.'
                )

            # Ensure no other override (active or inactive) exists for this date
            existing_overrides = WeeklyOverride.objects.filter(
                teacher=self.teacher, date=self.date
            )
            if self.pk:
                existing_overrides = existing_overrides.exclude(pk=self.pk)

            if existing_overrides.exists():
                raise ValidationError(
                    'An override entry already exists for this date.'
                )

        # Case 2: Active availability slot (is_available = True)
        else:
            if not self.start_time or not self.end_time:
                raise ValidationError({
                    'start_time': (
                        'Start and end times are required for active'
                        ' availability slots.'
                    )
                })

            if self.start_time >= self.end_time:
                raise ValidationError(
                    {'end_time': 'End time must be after start time.'}
                )

            # Prevent active slots if a full-day off override already exists for this date
            off_override = WeeklyOverride.objects.filter(
                teacher=self.teacher, date=self.date, is_available=False
            )
            if self.pk:
                off_override = off_override.exclude(pk=self.pk)

            if off_override.exists():
                raise ValidationError(
                    'This date is already marked as full-day off. Remove that'
                    ' entry first.'
                )

            # Check for overlapping active slots on the same date
            overlapping_qs = WeeklyOverride.objects.filter(
                teacher=self.teacher,
                date=self.date,
                is_available=True,
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )
            if self.pk:
                overlapping_qs = overlapping_qs.exclude(pk=self.pk)

            if overlapping_qs.exists():
                raise ValidationError(
                    'This time slot overlaps with another active availability'
                    ' slot on the same date.'
                )

    def __str__(self):
        status = "Available" if self.is_available else "Unavailable"
        return f"{self.date} ({status}): {self.start_time or ''} - {self.end_time or ''}"




class Booking(models.Model):
    class LessonType(models.TextChoices):
        TRIAL = 'trial', 'Trial Lesson'
        REGULAR = 'regular', 'Regular Lesson'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'
        DISPUTING = 'disputing', 'Disputing'

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bookings')
    teacher = models.ForeignKey('portfolio.TeacherProfile', on_delete=models.CASCADE, related_name='bookings')
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    lesson_type = models.CharField(max_length=10, choices=LessonType.choices, default=LessonType.REGULAR)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    cancellation_requested = models.BooleanField(default=False)
    completion_note = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Booking'
        verbose_name_plural = 'Bookings'
        constraints = [
            models.UniqueConstraint(
                fields=['teacher', 'date', 'start_time'],
                condition=models.Q(status__in=['pending', 'confirmed']),
                name='unique_active_teacher_datetime_booking',
            )
        ]

    def clean(self):
        super().clean()

        # 1. Basic time sanity check
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError({'end_time': 'End time must be after start time.'})

        # 2. Prevent a teacher from booking their own availability
        if self.teacher_id and self.student_id and self.teacher.user_id == self.student_id:
            raise ValidationError({'student': 'A teacher cannot book their own availability.'})

        # 3. Prevent overlapping active bookings for the same teacher
        if self.teacher_id and self.date and self.start_time and self.end_time:
            overlapping = Booking.objects.filter(
                teacher=self.teacher,
                date=self.date,
                status__in=[self.Status.PENDING, self.Status.CONFIRMED],
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)

            if overlapping.exists():
                raise ValidationError('This time overlaps with another booking for this teacher.')


    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def student_display_name(self):
        return self.student.get_full_name() or self.student.username

    @property
    def teacher_display_name(self):
        return self.teacher.user.get_full_name() or self.teacher.user.username

    @property
    def student_display_user(self):
        return self.student

    @property
    def teacher_display_user(self):
        return self.teacher.user

    @property
    def is_awaiting_resolution(self):
        lesson_end_datetime = timezone.make_aware(
            datetime.combine(self.date, self.end_time)
        )
        return self.status == self.Status.CONFIRMED and lesson_end_datetime < timezone.now()

    
    def __str__(self):
        return (
            f'Booking #{self.id} | {self.student} |'
            f' {self.get_lesson_type_display()} | {self.get_status_display()}'
        )



class Review(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='reviews',)
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='review')
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True, null=True)
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Review'
        verbose_name_plural = 'Reviews'

    def clean(self):
        super().clean()

        if hasattr(self, 'booking') and self.booking:
            # Ensure the booking is completed before allowing a review
            if self.booking.status != Booking.Status.COMPLETED:
                raise ValidationError('You can only review completed bookings.')

            # Ensure the student writing the review is the one who made the booking
            if hasattr(self, 'student') and self.student:
                if self.booking.student != self.student:
                    raise ValidationError('You can only review your own bookings.')

    def save(self, *args, **kwargs):
        self.full_clean()  
        super().save(*args, **kwargs)        

    def __str__(self):
        return f'Review for Booking #{self.booking.id} | Rating: {self.rating}'        