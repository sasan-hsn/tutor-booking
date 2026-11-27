from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse

from .legal import get_terms_gate_state
from .models import User


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


student_required = role_required(User.Role.STUDENT)
teacher_required = role_required(User.Role.TEACHER)


def _is_ajax_or_json_request(request) -> bool:
    return request.headers.get(
        "x-requested-with"
    ) == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")


def _enforce_terms_accepted(request, *, only_if_payments_enabled: bool = False):
    """Return a 403 JSON response or raise PermissionDenied if terms are unaccepted."""
    terms_required, terms_accepted = get_terms_gate_state(
        request.user,
        only_if_payments_enabled=only_if_payments_enabled,
    )
    if not terms_required or terms_accepted:
        return None

    if _is_ajax_or_json_request(request):
        return JsonResponse(
            {
                "error": "terms_not_accepted",
                "terms_version": settings.TERMS_CURRENT_VERSION,
            },
            status=403,
        )
    raise PermissionDenied("Terms of Service acceptance required.")


def terms_accepted_required(view_func=None, *, only_if_payments_enabled: bool = False):
    """
    Decorator enforcing acceptance of settings.TERMS_CURRENT_VERSION.
    Can be used as @terms_accepted_required or
    @terms_accepted_required(only_if_payments_enabled=True).
    """

    def decorator(func):
        @wraps(func)
        @login_required
        def wrapper(request, *args, **kwargs):
            denied_response = _enforce_terms_accepted(
                request,
                only_if_payments_enabled=only_if_payments_enabled,
            )
            if denied_response is not None:
                return denied_response
            return func(request, *args, **kwargs)

        return wrapper

    if view_func is not None:
        return decorator(view_func)
    return decorator


class TermsAcceptedRequiredMixin:
    """CBV mixin enforcing acceptance of settings.TERMS_CURRENT_VERSION."""

    only_if_payments_enabled = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        denied_response = _enforce_terms_accepted(
            request,
            only_if_payments_enabled=self.only_if_payments_enabled,
        )
        if denied_response is not None:
            return denied_response
        return super().dispatch(request, *args, **kwargs)
