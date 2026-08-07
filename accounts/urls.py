from django.urls import path
from . import views

app_name = 'accounts'
urlpatterns = [
    path('signup/', views.student_signup, name='student_signup'),
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
]