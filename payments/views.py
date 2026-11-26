import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import teacher_required
from accounts.models import Tenant
from booking.models import Booking
from payments.forms import (
    DUPLICATE_REFERENCE_ERROR,
    ManualTopUpForm,
    get_user_display_name,
)
from payments.models import Order, Payment
from payments.services import build_manual_external_id, process_manual_topup
from wallet.models import LedgerEntry, Wallet

User = get_user_model()


def _get_tenant_for_teacher(teacher) -> Tenant:
    tenant, _ = Tenant.objects.get_or_create(
        teacher_profile=teacher,
        defaults={
            "name": get_user_display_name(teacher.user) or f"Teacher {teacher.id}",
        },
    )
    return tenant


def _to_viewer_local(dt: datetime | None, viewer_tz: ZoneInfo) -> datetime | None:
    if dt is None:
        return None
    return timezone.localtime(dt, viewer_tz).replace(tzinfo=None)


_MANUAL_PREFIX_RE = re.compile(r"^tenant_\d+:manual_")
_MANUAL_TOPUP_DESC_RE = re.compile(r"^Manual top-up:\s*tenant_\d+:manual_")


def _strip_manual_prefix(reference_id: str) -> str:
    return _MANUAL_PREFIX_RE.sub("", reference_id or "")


def _attach_ledger_display_fields(
    entries: list[LedgerEntry], tenant: Tenant, viewer_tz: ZoneInfo
) -> None:
    manual_refs = [
        e.reference_id for e in entries if _MANUAL_PREFIX_RE.match(e.reference_id or "")
    ]
    notes_by_external_id: dict[str, str] = {}
    if manual_refs:
        for payment in Payment.objects.filter(
            tenant=tenant,
            external_id__in=manual_refs,
        ).only("external_id", "raw_metadata"):
            note = ((payment.raw_metadata or {}).get("notes") or "").strip()
            if note:
                notes_by_external_id[payment.external_id] = note

    for entry in entries:
        entry.local_created_at = _to_viewer_local(entry.created_at, viewer_tz)
        entry.student_display_name = get_user_display_name(entry.wallet.user)
        entry.display_reference = _strip_manual_prefix(entry.reference_id)

        custom_note = notes_by_external_id.get(entry.reference_id)
        if custom_note:
            entry.display_description = custom_note
        elif _MANUAL_TOPUP_DESC_RE.match(entry.description or ""):
            entry.display_description = "Manual top-up"
        else:
            entry.display_description = entry.description


def _build_finance_context(tenant: Tenant, teacher, viewer_tz: ZoneInfo) -> dict:
    wallet_aggs = Wallet.objects.filter(
        tenant=tenant,
        user__role=User.Role.STUDENT,
    ).aggregate(
        total_settled=Sum("settled_balance"),
        total_reserved=Sum("reserved_balance"),
    )
    total_settled = wallet_aggs["total_settled"] or Decimal("0.00")
    total_reserved = wallet_aggs["total_reserved"] or Decimal("0.00")
    total_available = total_settled - total_reserved

    hold_aggs = Booking.objects.filter(
        teacher=teacher,
        payment_status=Booking.PaymentStatus.RESERVED,
    ).aggregate(
        active_holds_total=Sum("held_amount"),
        active_holds_count=Count("id"),
    )
    active_holds_total = hold_aggs["active_holds_total"] or Decimal("0.00")
    active_holds_count = hold_aggs["active_holds_count"] or 0

    recent_ledger_entries = list(
        LedgerEntry.objects.filter(tenant=tenant)
        .select_related("wallet", "wallet__user")
        .order_by("-created_at", "-id")[:25]
    )
    _attach_ledger_display_fields(recent_ledger_entries, tenant, viewer_tz)

    claims_queue = list(
        Order.objects.filter(
            tenant=tenant,
            status=Order.Status.PENDING,
            claimed_paid_at__isnull=False,
        )
        .select_related("student")
        .order_by("-claimed_paid_at", "-created_at")
    )
    for claim in claims_queue:
        claim.local_claimed_paid_at = _to_viewer_local(claim.claimed_paid_at, viewer_tz)
        claim.student_display_name = get_user_display_name(claim.student)
        claim.prefill_notes = f"Top-up claim for Order #{claim.id}"

    return {
        "teacher": teacher,
        "tenant": tenant,
        "total_settled": total_settled,
        "total_reserved": total_reserved,
        "total_available": total_available,
        "active_holds_total": active_holds_total,
        "active_holds_count": active_holds_count,
        "recent_ledger_entries": recent_ledger_entries,
        "claims_queue": claims_queue,
        "claims_count": len(claims_queue),
    }


def _first_form_error(form: ManualTopUpForm) -> str:
    if "reference_id" in form.errors:
        return form.errors["reference_id"][0]
    for field_errors in form.errors.values():
        if field_errors:
            return field_errors[0]
    return "Please check the form inputs and try again."


@teacher_required
def teacher_finance_dashboard(request):
    teacher = request.user.teacher_profile
    tenant = _get_tenant_for_teacher(teacher)
    viewer_tz = ZoneInfo(request.user.timezone)
    context = _build_finance_context(tenant, teacher, viewer_tz)
    context["form"] = ManualTopUpForm(tenant=tenant)
    return render(request, "payments/teacher_finance.html", context)


@teacher_required
@require_POST
def manual_topup(request):
    teacher = request.user.teacher_profile
    tenant = _get_tenant_for_teacher(teacher)
    form = ManualTopUpForm(request.POST, tenant=tenant)

    if not form.is_valid():
        return JsonResponse(
            {
                "success": False,
                "error": _first_form_error(form),
                "errors": form.errors,
            },
            status=400,
        )

    cleaned = form.cleaned_data
    student = cleaned["student"]
    amount = cleaned["amount"]
    reference_id = cleaned["reference_id"]
    notes = cleaned.get("notes", "")
    idempotency_key = cleaned.get("idempotency_key") or None

    try:
        payment = process_manual_topup(
            tenant=tenant,
            student=student,
            amount=amount,
            reference_id=reference_id,
            admin_user=request.user,
            notes=notes,
            idempotency_key=idempotency_key,
        )
    except IntegrityError:
        existing = Payment.objects.filter(
            external_id=build_manual_external_id(tenant.id, reference_id)
        ).first()
        existing_idem = (
            (existing.raw_metadata or {}).get("idempotency_key") if existing else None
        )
        if existing and idempotency_key and existing_idem == idempotency_key:
            payment = existing
        else:
            return JsonResponse(
                {
                    "success": False,
                    "error": DUPLICATE_REFERENCE_ERROR,
                    "errors": {"reference_id": [DUPLICATE_REFERENCE_ERROR]},
                },
                status=400,
            )
    except ValidationError as exc:
        msg = exc.message if hasattr(exc, "message") else str(exc)
        return JsonResponse(
            {"success": False, "error": msg, "errors": {"__all__": [msg]}},
            status=400,
        )

    viewer_tz = ZoneInfo(request.user.timezone)
    context = _build_finance_context(tenant, teacher, viewer_tz)
    student_name = get_user_display_name(student)

    return JsonResponse(
        {
            "success": True,
            "message": (
                f"Credited ${payment.net_amount:.2f} {payment.currency} to {student_name}."
            ),
            "payment_id": payment.id,
            "overview_html": render_to_string(
                "payments/partials/_finance_overview.html", context, request=request
            ),
            "ledger_html": render_to_string(
                "payments/partials/_finance_ledger_table.html", context, request=request
            ),
            "claims_html": render_to_string(
                "payments/partials/_finance_claims_queue.html", context, request=request
            ),
        }
    )
