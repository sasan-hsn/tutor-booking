from django.urls import path
from . import views

app_name = "booking"
urlpatterns = [
  path("student/", views.student_dashboard, name="student_dashboard"),
 ]
