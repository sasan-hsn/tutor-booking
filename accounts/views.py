from django.contrib.auth.forms import AuthenticationForm
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
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
            return redirect('booking:student_dashboard')
    else:
        form = StudentSignUpForm()  
    return render(request, 'accounts/student_signup.html', {'form': form})


def user_login(request):
    next_url = request.POST.get('next') or request.GET.get('next')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)

            if user.role == User.Role.TEACHER:
                return redirect('booking:teacher_dashboard') 
            else:
                return redirect('booking:student_dashboard')
    else:
        form = AuthenticationForm()
    return render(request, 'accounts/login.html', {'form': form, 'next': next_url or ''})


@require_POST
def user_logout(request):
    logout(request)
    return redirect('/')  