from django.urls import path
from . import views

app_name = "chat"

urlpatterns = [
    path("with/<str:teacher_username>/", views.start_conversation, name="start_conversation"),
    path("<uuid:conversation_id>/", views.conversation_detail, name="conversation_detail"),
    path("<uuid:conversation_id>/send/", views.send_message_view, name="send_message"),
]
