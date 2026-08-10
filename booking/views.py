from django.shortcuts import render
from accounts.decorators import student_required

@student_required
def dashboard_preview(request):
    return render(request, "dashboard_preview.html")