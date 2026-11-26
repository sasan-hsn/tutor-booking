from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.teacher_finance_dashboard, name="teacher_finance"),
    path("manual-topup/", views.manual_topup, name="manual_topup"),
]
