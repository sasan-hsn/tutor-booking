from django.urls import path
from . import views

app_name = "portfolio"

urlpatterns = [
    path("", views.landing_page, name="landing_page"),
    path('teacher/settings/account/', views.teacher_settings_account, name='teacher_settings_account'),
    path('teacher/settings/portfolio/', views.teacher_settings_portfolio, name='teacher_settings_portfolio'),
    path('teacher/settings/booking/', views.teacher_settings_booking, name='teacher_settings_booking'),
    path('teacher/certificates/add/', views.teacher_certificate_add, name='teacher_certificate_add'),
    path('teacher/certificates/<int:certificate_id>/delete/', views.teacher_certificate_delete, name='teacher_certificate_delete'),
]