from django.urls import path
from . import views

app_name = "booking"
urlpatterns = [
  path("preview/", views.dashboard_preview, name="dashboard_preview"),
 ]
