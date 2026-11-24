from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError

if TYPE_CHECKING:
    from accounts.models import User


def get_active_superuser(username: str) -> "User":
    """
    Looks up a user by username and validates they are an active superuser.
    Raises CommandError if user does not exist or lacks superuser/active status.
    """
    user_model = get_user_model()
    try:
        user = user_model.objects.get(username=username)
    except user_model.DoesNotExist:
        raise CommandError(f"Admin user '{username}' does not exist.")

    if not user.is_superuser or not user.is_active:
        raise CommandError(f"User '{username}' must be an active superuser.")

    return user
