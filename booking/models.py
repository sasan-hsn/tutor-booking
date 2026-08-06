from django.db import models
from django.core.exceptions import ValidationError


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



class AvailabilitySlot(models.Model):
    teacher = models.ForeignKey(
        'portfolio.TeacherProfile',
        on_delete=models.CASCADE,
        related_name='availability_slots'
    )
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_booked = models.BooleanField(default=False)

    class Meta:
        ordering = ['date', 'start_time']
        verbose_name = 'Availability Slot'
        constraints = [
            models.UniqueConstraint(
                fields=['teacher', 'date', 'start_time'],
                name='unique_teacher_slot_per_start_time',
            )
        ]

    def clean(self):
        super().clean()

        if self.start_time >= self.end_time:
            raise ValidationError({
                'end_time': 'End time must be after start time.'
            })

        # Check for overlapping slots for the same teacher on the same date
        overlapping_qs = AvailabilitySlot.objects.filter(
            teacher=self.teacher,
            date=self.date,
            start_time__lt=self.end_time,
            end_time__gt=self.start_time,
        )
        if self.pk:
            overlapping_qs = overlapping_qs.exclude(pk=self.pk)

        if overlapping_qs.exists():
            raise ValidationError(
                'This time slot overlaps with another availability slot on the same date.'
            )

    
    def __str__(self):
        status = 'Booked' if self.is_booked else 'Available'
        return (
            f'{self.teacher} | {self.date} |'
            f" {self.start_time.strftime('%H:%M')} -"
            f" {self.end_time.strftime('%H:%M')} | {status}"
        )