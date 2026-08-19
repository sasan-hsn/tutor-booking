from django.urls import path
from . import views

app_name = "booking"
urlpatterns = [
    path("student/", views.student_dashboard, name="student_dashboard"),
    path("student/booking/", views.student_booking, name="student_booking"),
    path("student/book/week/", views.student_booking_week_ajax, name="student_booking_week_ajax"),
    path("student/book-slot/", views.book_slot, name="book_slot"),
    path('teacher/', views.teacher_dashboard, name='teacher_dashboard'),
    path('teacher/requests/', views.teacher_lesson_requests, name='teacher_lesson_requests'),
    path('teacher/requests/<int:booking_id>/respond/', views.respond_to_booking, name='respond_to_booking'),
    path('teacher/students/', views.student_management, name='student_management'),
    path('teacher/schedule/regular', views.teacher_regular_schedule, name='teacher_regular_schedule'),
    path('teacher/schedule/regular/add/', views.teacher_regular_schedule_add, name='teacher_regular_schedule_add'),
    path('teacher/schedule/regular/<int:availability_id>/delete/', views.teacher_regular_schedule_delete, name='teacher_regular_schedule_delete'),
    path('teacher/schedule/overrides/', views.teacher_weekly_override, name='teacher_weekly_override'),
    path('teacher/schedule/overrides/add/', views.teacher_weekly_override_add, name='teacher_weekly_override_add'),
    path('teacher/schedule/overrides/<int:override_id>/delete/', views.teacher_weekly_override_delete, name='teacher_weekly_override_delete'),
 ]
