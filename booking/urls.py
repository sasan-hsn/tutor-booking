from django.urls import path
from . import views

app_name = "booking"
urlpatterns = [
  path("student/", views.student_dashboard, name="student_dashboard"),
  path("student/booking/", views.student_booking, name="student_booking"),
  path("student/book/week/", views.student_booking_week_ajax, name="student_booking_week_ajax"),
  path("student/book-slot/", views.book_slot, name="book_slot"),
 ]
