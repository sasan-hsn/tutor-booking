from zoneinfo import ZoneInfo

from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts.decorators import student_required
from wallet.models import LedgerEntry
from wallet.services import (
    STUDENT_LEDGER_CATEGORIES,
    attach_student_ledger_display_fields,
    get_entry_types_for_student_category,
    get_student_wallet_snapshot,
)

STUDENT_LEDGER_PAGE_SIZE = 20


@student_required
@require_GET
def student_wallet(request):
    """Display the authenticated student's wallet summary and paginated ledger history."""
    snapshot = get_student_wallet_snapshot(request.user, request=request)

    raw_type = (request.GET.get("type") or "").strip()
    filtered_entry_types = get_entry_types_for_student_category(raw_type)
    active_type_filter = raw_type.upper() if filtered_entry_types is not None else ""

    if snapshot.tenant is not None and snapshot.wallet is not None:
        entries_qs = LedgerEntry.objects.filter(
            tenant=snapshot.tenant,
            wallet=snapshot.wallet,
        ).order_by("-created_at", "-id")
        if filtered_entry_types is not None:
            entries_qs = entries_qs.filter(entry_type__in=filtered_entry_types)
    else:
        entries_qs = LedgerEntry.objects.none()

    paginator = Paginator(entries_qs, STUDENT_LEDGER_PAGE_SIZE)
    ledger_page = paginator.get_page(request.GET.get("page", 1))
    viewer_tz = ZoneInfo(getattr(request.user, "timezone", "UTC") or "UTC")
    attach_student_ledger_display_fields(list(ledger_page.object_list), viewer_tz)

    context = {
        "wallet_snapshot": snapshot,
        "ledger_entries": ledger_page,
        "ledger_categories": STUDENT_LEDGER_CATEGORIES,
        "active_type_filter": active_type_filter,
        "total_entries_count": paginator.count,
    }
    return render(request, "wallet/student_wallet.html", context)
