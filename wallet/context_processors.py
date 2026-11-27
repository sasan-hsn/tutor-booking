from django.contrib.auth import get_user_model

from wallet.services import get_student_wallet_snapshot

User = get_user_model()


def student_wallet_context(request):
    """
    Expose the authenticated student's wallet balance summary for the current tenant
    to templates. Short-circuits with zero queries for anonymous or non-student users.
    """
    user = getattr(request, "user", None)
    if (
        not user
        or not getattr(user, "is_authenticated", False)
        or getattr(user, "role", None) != User.Role.STUDENT
    ):
        return {"student_wallet_summary": None}

    snapshot = get_student_wallet_snapshot(user, request=request)
    if snapshot.tenant is None:
        return {"student_wallet_summary": None}

    return {"student_wallet_summary": snapshot}
