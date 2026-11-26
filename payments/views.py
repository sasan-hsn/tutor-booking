import re
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Count, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.decorators import teacher_required
from accounts.models import Tenant
from booking.models import Booking
from booking.services import DisputeConflictError, resolve_dispute
from payments.exceptions import PayoutConflictError, PayoutInsufficientBalanceError
from payments.forms import (
    DUPLICATE_REFERENCE_ERROR,
    CompletePayoutForm,
    ManualTopUpForm,
    RejectPayoutForm,
    ResolveDisputeForm,
)
from payments.models import Order, Payment, Payout
from payments.services import (
    DUPLICATE_PAYOUT_EXTERNAL_REF_ERROR,
    attach_ledger_display_fields,
    build_manual_external_id,
    build_tenant_ledger_csv,
    get_user_display_name,
    has_duplicate_payout_external_ref,
    mark_payout_processing,
    process_manual_topup,
    release_payout,
    settle_payout,
    to_viewer_local,
)
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


_REJECT_DESC_RE = re.compile(r"^Payout rejected \((.+)\): payout #\d+$")


def _extract_rejection_reason(description: str) -> str:
    match = _REJECT_DESC_RE.match(description or "")
    return match.group(1) if match else ""


def _attach_payout_display_fields(
    payouts: list[Payout],
    tenant: Tenant,
    viewer_tz: ZoneInfo,
    *,
    include_rejection_reasons: bool = False,
) -> None:
    reasons_by_payout_ref: dict[str, str] = {}
    if include_rejection_reasons:
        rejected_refs = [
            f"payout_{p.id}" for p in payouts if p.status == Payout.Status.REJECTED
        ]
        if rejected_refs:
            for entry in LedgerEntry.objects.filter(
                tenant=tenant,
                entry_type=LedgerEntry.EntryType.PAYOUT_RELEASE,
                reference_id__in=rejected_refs,
            ).only("reference_id", "description"):
                reason = _extract_rejection_reason(entry.description)
                if reason:
                    reasons_by_payout_ref[entry.reference_id] = reason

    for payout in payouts:
        payout.student_display_name = get_user_display_name(payout.student)
        payout.local_requested_at = to_viewer_local(
            payout.confirmation_used_at or payout.created_at, viewer_tz
        )
        payout.local_processed_at = to_viewer_local(payout.processed_at, viewer_tz)
        payout.rejection_reason = reasons_by_payout_ref.get(f"payout_{payout.id}", "")


def _format_dispute_countdown(deadline_at, now) -> str:
    if deadline_at is None:
        return "—"
    remaining_seconds = int((deadline_at - now).total_seconds())
    if remaining_seconds <= 0:
        return "Auto-expiring"
    days, rem = divmod(remaining_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}d {hours}h left"
    if hours > 0:
        return f"{hours}h {minutes}m left"
    return f"{max(minutes, 1)}m left"


def _attach_dispute_display_fields(
    disputes: list[Booking], viewer_tz: ZoneInfo
) -> None:
    now = timezone.now()
    dispute_window_days = getattr(settings, "DISPUTE_WINDOW_DAYS", 7)
    for booking in disputes:
        booking.local_start_at = to_viewer_local(booking.start_at, viewer_tz)
        booking.local_end_at = to_viewer_local(booking.end_at, viewer_tz)
        booking.local_dispute_opened_at = to_viewer_local(
            booking.dispute_opened_at, viewer_tz
        )
        deadline = (
            booking.dispute_opened_at + timedelta(days=dispute_window_days)
            if booking.dispute_opened_at
            else None
        )
        booking.dispute_deadline_iso = deadline.isoformat() if deadline else ""
        booking.dispute_countdown_label = _format_dispute_countdown(deadline, now)


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
    attach_ledger_display_fields(recent_ledger_entries, tenant, viewer_tz)

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
        claim.local_claimed_paid_at = to_viewer_local(claim.claimed_paid_at, viewer_tz)
        claim.student_display_name = get_user_display_name(claim.student)
        claim.prefill_notes = f"Top-up claim for Order #{claim.id}"

    payouts_queue = list(
        Payout.objects.filter(
            tenant=tenant,
            status__in=[Payout.Status.REQUESTED, Payout.Status.PROCESSING],
        )
        .select_related("student")
        .order_by("created_at", "id")
    )
    _attach_payout_display_fields(payouts_queue, tenant, viewer_tz)

    payouts_history = list(
        Payout.objects.filter(
            tenant=tenant,
            status__in=[Payout.Status.COMPLETED, Payout.Status.REJECTED],
        )
        .select_related("student")
        .order_by("-processed_at", "-created_at", "-id")[:25]
    )
    _attach_payout_display_fields(
        payouts_history, tenant, viewer_tz, include_rejection_reasons=True
    )

    disputes_queue = list(
        Booking.objects.filter(
            teacher=teacher,
            status=Booking.Status.DISPUTING,
        )
        .select_related("student")
        .order_by("dispute_opened_at", "id")
    )
    _attach_dispute_display_fields(disputes_queue, viewer_tz)

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
        "payouts_queue": payouts_queue,
        "payouts_queue_count": len(payouts_queue),
        "payouts_history": payouts_history,
        "disputes_queue": disputes_queue,
        "disputes_queue_count": len(disputes_queue),
    }


def _first_form_error(form) -> str:
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
    context["complete_payout_form"] = CompletePayoutForm()
    context["reject_payout_form"] = RejectPayoutForm()
    context["resolve_dispute_form"] = ResolveDisputeForm()
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


def _get_tenant_payout_or_404(tenant: Tenant, payout_id: int) -> Payout:
    return get_object_or_404(
        Payout.objects.select_related("student", "tenant"),
        pk=payout_id,
        tenant=tenant,
    )


def _validation_error_message(exc: ValidationError) -> str:
    if hasattr(exc, "messages") and exc.messages:
        return exc.messages[0]
    if hasattr(exc, "message"):
        return exc.message
    return str(exc)


def _payout_action_success_response(
    request,
    tenant: Tenant,
    teacher,
    payout: Payout,
    message: str,
) -> JsonResponse:
    viewer_tz = ZoneInfo(request.user.timezone)
    context = _build_finance_context(tenant, teacher, viewer_tz)
    return JsonResponse(
        {
            "success": True,
            "message": message,
            "payout_id": payout.id,
            "payout_status": payout.status,
            "overview_html": render_to_string(
                "payments/partials/_finance_overview.html", context, request=request
            ),
            "payouts_html": render_to_string(
                "payments/partials/_finance_payouts_queue.html",
                context,
                request=request,
            ),
            "ledger_html": render_to_string(
                "payments/partials/_finance_ledger_table.html", context, request=request
            ),
        }
    )


def _run_payout_transition(
    request,
    payout_id: int,
    *,
    form_class=None,
    form_kwargs_fn=None,
    error_field: str = "__all__",
    action_fn,
    success_message_fn,
) -> JsonResponse:
    """Shared handler for teacher payout review queue POST transitions."""
    teacher = request.user.teacher_profile
    tenant = _get_tenant_for_teacher(teacher)
    payout = _get_tenant_payout_or_404(tenant, payout_id)

    cleaned_data = {}
    if form_class is not None:
        kwargs = form_kwargs_fn(payout) if form_kwargs_fn else {}
        form = form_class(request.POST, **kwargs)
        if not form.is_valid():
            return JsonResponse(
                {
                    "success": False,
                    "error": _first_form_error(form),
                    "errors": form.errors,
                },
                status=400,
            )
        cleaned_data = form.cleaned_data

    try:
        action_fn(tenant, payout, cleaned_data)
    except PayoutConflictError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=409)
    except IntegrityError as exc:
        is_duplicate_ref = "unique_payout_provider_external_ref" in str(exc) or (
            error_field == "txhash"
            and cleaned_data.get("txhash")
            and has_duplicate_payout_external_ref(
                provider=payout.provider,
                external_ref=cleaned_data["txhash"],
                exclude_payout_id=payout.pk,
            )
        )
        if is_duplicate_ref:
            return JsonResponse(
                {
                    "success": False,
                    "error": DUPLICATE_PAYOUT_EXTERNAL_REF_ERROR,
                    "errors": {"txhash": [DUPLICATE_PAYOUT_EXTERNAL_REF_ERROR]},
                },
                status=400,
            )
        raise
    except (PayoutInsufficientBalanceError, ValidationError) as exc:
        msg = _validation_error_message(exc)
        return JsonResponse(
            {"success": False, "error": msg, "errors": {error_field: [msg]}},
            status=400,
        )

    return _payout_action_success_response(
        request,
        tenant,
        teacher,
        payout,
        success_message_fn(tenant, payout),
    )


@teacher_required
@require_POST
def payout_mark_processing(request, payout_id: int):
    return _run_payout_transition(
        request,
        payout_id,
        action_fn=lambda tenant, payout, _: mark_payout_processing(
            tenant=tenant,
            payout=payout,
            admin_user=request.user,
        ),
        success_message_fn=lambda _tenant, payout: (
            f"Payout #{payout.id} for {get_user_display_name(payout.student)} marked as processing."
        ),
    )


@teacher_required
@require_POST
def payout_complete(request, payout_id: int):
    return _run_payout_transition(
        request,
        payout_id,
        form_class=CompletePayoutForm,
        form_kwargs_fn=lambda payout: {"payout": payout},
        error_field="txhash",
        action_fn=lambda tenant, payout, data: settle_payout(
            tenant=tenant,
            payout=payout,
            external_ref=data["txhash"],
            admin_user=request.user,
        ),
        success_message_fn=lambda tenant, payout: (
            f"Completed payout #{payout.id} (${payout.amount_net:.2f} {tenant.default_currency}) "
            f"for {get_user_display_name(payout.student)}."
        ),
    )


@teacher_required
@require_POST
def payout_reject(request, payout_id: int):
    return _run_payout_transition(
        request,
        payout_id,
        form_class=RejectPayoutForm,
        error_field="reason",
        action_fn=lambda tenant, payout, data: release_payout(
            tenant=tenant,
            payout=payout,
            reason=data["reason"],
            admin_user=request.user,
        ),
        success_message_fn=lambda tenant, payout: (
            f"Rejected payout #{payout.id} and restored "
            f"${payout.amount_gross:.2f} {tenant.default_currency} "
            f"to {get_user_display_name(payout.student)}."
        ),
    )


@teacher_required
@require_POST
def dispute_resolve(request, booking_id: int):
    teacher = request.user.teacher_profile
    tenant = _get_tenant_for_teacher(teacher)
    booking = get_object_or_404(
        Booking.objects.select_related("student", "teacher__user"),
        pk=booking_id,
        teacher=teacher,
    )

    form = ResolveDisputeForm(request.POST)
    if not form.is_valid():
        return JsonResponse(
            {
                "success": False,
                "error": _first_form_error(form),
                "errors": form.errors,
            },
            status=400,
        )

    resolution = form.cleaned_data["resolution"]
    try:
        resolved_booking = resolve_dispute(
            tenant=tenant,
            booking=booking,
            teacher_user=request.user,
            resolution=resolution,
        )
    except DisputeConflictError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=409)
    except ValidationError as exc:
        msg = _validation_error_message(exc)
        return JsonResponse(
            {"success": False, "error": msg, "errors": {"__all__": [msg]}},
            status=400,
        )

    viewer_tz = ZoneInfo(request.user.timezone)
    context = _build_finance_context(tenant, teacher, viewer_tz)
    student_name = get_user_display_name(resolved_booking.student)
    if resolution == Booking.DisputeResolution.STUDENT_FAVOR:
        message = (
            f"Resolved dispute for Booking #{resolved_booking.id} in student favor; "
            f"released ${resolved_booking.held_amount:.2f} {tenant.default_currency} "
            f"to {student_name}."
        )
    else:
        message = (
            f"Resolved dispute for Booking #{resolved_booking.id} in teacher favor; "
            f"settled ${resolved_booking.held_amount:.2f} {tenant.default_currency}."
        )

    return JsonResponse(
        {
            "success": True,
            "message": message,
            "booking_id": resolved_booking.id,
            "booking_status": resolved_booking.status,
            "dispute_resolution": resolved_booking.dispute_resolution,
            "overview_html": render_to_string(
                "payments/partials/_finance_overview.html", context, request=request
            ),
            "disputes_html": render_to_string(
                "payments/partials/_finance_disputes_queue.html",
                context,
                request=request,
            ),
            "ledger_html": render_to_string(
                "payments/partials/_finance_ledger_table.html", context, request=request
            ),
        }
    )


@teacher_required
@require_GET
def export_ledger_csv(request):
    teacher = request.user.teacher_profile
    tenant = _get_tenant_for_teacher(teacher)
    viewer_tz = ZoneInfo(request.user.timezone)
    csv_content = build_tenant_ledger_csv(tenant, viewer_tz)
    local_date = timezone.localtime(timezone.now(), viewer_tz).date().isoformat()
    response = HttpResponse(csv_content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="ledger-export-{local_date}.csv"'
    )
    return response
