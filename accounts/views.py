from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import render
from django.contrib.auth import login, logout
from django.shortcuts import redirect
from .forms import StudentSignUpForm
from .models import User


def student_signup(request):
    if request.method == 'POST':
        form = StudentSignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)  
            return redirect('/')  # TODO: replace with student_dashboard when built
    else:
        form = StudentSignUpForm()  
    return render(request, 'accounts/student_signup.html', {'form': form})


def user_login(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            if user.role == User.Role.TEACHER:
                return redirect('/')  # TODO: replace with teacher_dashboard when built
            else:
                return redirect('/')  # TODO: replace with student_dashboard when built
    else:
        form = AuthenticationForm()
    return render(request, 'accounts/login.html', {'form': form})


def user_logout(request):
    logout(request)
    return redirect('/')  # Redirect to home page after logout  