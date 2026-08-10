from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(role):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(request, *args, **kwargs):
            if request.user.role != role:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


student_required = role_required("student")
teacher_required = role_required("teacher")