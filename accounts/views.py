from django.shortcuts import render
from django.contrib.auth import login
from django.shortcuts import redirect
from .forms import StudentSignUpForm


def student_signup(request):
    if request.method == 'POST':
        form = StudentSignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)  
            return redirect('/')
    else:
        form = StudentSignUpForm()  
    return render(request, 'accounts/student_signup.html', {'form': form})