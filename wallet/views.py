from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts.decorators import student_required
from accounts.legal import (
    get_terms_gate_state,
    load_terms_markdown,
    render_terms_html,
)
from payments.services import (
    build_topup_order_payload,
    get_active_crypto_receiving_address,
    get_active_pending_order,
)
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

    terms_required, terms_accepted = get_terms_gate_state(
        request.user,
        tenant=snapshot.tenant,
    )
    active_address = (
        get_active_crypto_receiving_address(snapshot.tenant)
        if snapshot.tenant is not None
        else None
    )
    active_order = (
        get_active_pending_order(snapshot.tenant, request.user)
        if snapshot.tenant is not None
        else None
    )
    topup_active_order_data = (
        build_topup_order_payload(
            active_order,
            snapshot.tenant,
            viewer_tz,
            include_instructions=False,
        )
        if active_order is not None and snapshot.tenant is not None
        else None
    )

    topup_min_amount = (
        snapshot.tenant.min_topup_amount
        if snapshot.tenant is not None
        else settings.MIN_TOPUP_AMOUNT
    )

    context = {
        "wallet_snapshot": snapshot,
        "ledger_entries": ledger_page,
        "ledger_categories": STUDENT_LEDGER_CATEGORIES,
        "active_type_filter": active_type_filter,
        "total_entries_count": paginator.count,
        "terms_required": terms_required,
        "terms_accepted": terms_accepted,
        "terms_version": settings.TERMS_CURRENT_VERSION,
        "terms_html": render_terms_html(load_terms_markdown()),
        "topup_has_active_address": active_address is not None,
        "topup_min_amount": topup_min_amount,
        "topup_expiry_hours": getattr(settings, "ORDER_EXPIRY_HOURS", 24),
        "topup_active_order_data": topup_active_order_data,
    }
    return render(request, "wallet/student_wallet.html", context)
