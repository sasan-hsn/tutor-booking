from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.db.models import Count, Sum
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.decorators import (
    _is_ajax_or_json_request,
    student_required,
    teacher_required,
    terms_accepted_required,
)
from accounts.legal import get_default_tenant
from accounts.models import Tenant
from booking.models import Booking
from booking.services import DisputeConflictError, resolve_dispute
from payments.exceptions import (
    PayoutConflictError,
    PayoutInsufficientBalanceError,
    TopupOrderConflictError,
)
from payments.forms import (
    DUPLICATE_REFERENCE_ERROR,
    CompletePayoutForm,
    ManualTopUpForm,
    ReceivingAddressForm,
    RejectPayoutForm,
    ResolveDisputeForm,
    StudentRefundRequestForm,
    StudentTopUpClaimForm,
    StudentTopUpInitiateForm,
)
from payments.models import Order, Payment, PaymentAddress, Payout
from payments.services import (
    DEFAULT_RECEIVING_ADDRESS_PROVIDER,
    DUPLICATE_PAYOUT_EXTERNAL_REF_ERROR,
    attach_ledger_display_fields,
    attach_payout_display_fields,
    build_manual_external_id,
    build_tenant_ledger_csv,
    build_topup_order_payload,
    cancel_pending_topup_order,
    cancel_student_payout_request,
    create_payout_request,
    create_topup_order,
    get_active_pending_order,
    get_payout_by_raw_token,
    get_student_payouts_for_display,
    get_user_display_name,
    has_duplicate_payout_external_ref,
    inspect_payout_confirmation_state,
    mark_payout_processing,
    process_manual_topup,
    release_payout,
    settle_payout,
    submit_topup_claim,
    to_viewer_local,
    update_receiving_address,
    verify_and_reserve_payout,
)
from wallet.exceptions import WalletFrozenError
from wallet.models import LedgerEntry, Wallet
from wallet.services import get_student_wallet_snapshot

User = get_user_model()


def _get_tenant_for_teacher(teacher) -> Tenant:
    tenant, _ = Tenant.objects.get_or_create(
        teacher_profile=teacher,
        defaults={
            "name": get_user_display_name(teacher.user) or f"Teacher {teacher.id}",
        },
    )
    return tenant


def _format_remaining_countdown(
    deadline_at, now, *, expired_label: str = "Auto-expiring"
) -> str:
    if deadline_at is None:
        return "—"
    remaining_seconds = int((deadline_at - now).total_seconds())
    if remaining_seconds <= 0:
        return expired_label
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
        booking.dispute_countdown_label = _format_remaining_countdown(deadline, now)


def _attach_receiving_address_display_fields(
    tenant: Tenant, viewer_tz: ZoneInfo
) -> tuple[PaymentAddress | None, PaymentAddress | None]:
    now = timezone.now()
    addresses = list(
        PaymentAddress.objects.filter(
            tenant=tenant,
            provider=DEFAULT_RECEIVING_ADDRESS_PROVIDER,
            status__in=[
                PaymentAddress.Status.ACTIVE,
                PaymentAddress.Status.PENDING_ACTIVATION,
            ],
        ).order_by("-created_at", "-id")
    )
    active_addr = next(
        (a for a in addresses if a.status == PaymentAddress.Status.ACTIVE),
        None,
    )
    if active_addr is not None:
        active_addr.local_activates_at = to_viewer_local(
            active_addr.activates_at or active_addr.created_at, viewer_tz
        )

    pending_addr = next(
        (a for a in addresses if a.status == PaymentAddress.Status.PENDING_ACTIVATION),
        None,
    )
    if pending_addr is not None:
        pending_addr.local_activates_at = to_viewer_local(
            pending_addr.activates_at, viewer_tz
        )
        pending_addr.activates_at_iso = (
            pending_addr.activates_at.isoformat() if pending_addr.activates_at else ""
        )
        pending_addr.activation_countdown_label = _format_remaining_countdown(
            pending_addr.activates_at,
            now,
            expired_label="Activating soon",
        )

    return active_addr, pending_addr


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
    attach_payout_display_fields(payouts_queue, tenant, viewer_tz)

    payouts_history = list(
        Payout.objects.filter(
            tenant=tenant,
            status__in=[Payout.Status.COMPLETED, Payout.Status.REJECTED],
        )
        .select_related("student")
        .order_by("-processed_at", "-created_at", "-id")[:25]
    )
    attach_payout_display_fields(
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

    active_receiving_address, pending_receiving_address = (
        _attach_receiving_address_display_fields(tenant, viewer_tz)
    )

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
        "active_receiving_address": active_receiving_address,
        "pending_receiving_address": pending_receiving_address,
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
    context["receiving_address_form"] = ReceivingAddressForm()
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


@teacher_required
@require_POST
def receiving_address_update(request):
    teacher = request.user.teacher_profile
    tenant = _get_tenant_for_teacher(teacher)
    form = ReceivingAddressForm(request.POST)

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
    try:
        address_obj = update_receiving_address(
            tenant=tenant,
            user=request.user,
            raw_address=cleaned["address"],
            current_password=cleaned["current_password"],
        )
    except PermissionDenied as exc:
        msg = str(exc) or "Incorrect password."
        return JsonResponse(
            {
                "success": False,
                "error": msg,
                "errors": {"current_password": [msg]},
            },
            status=403,
        )
    except ValidationError as exc:
        msg = _validation_error_message(exc)
        return JsonResponse(
            {
                "success": False,
                "error": msg,
                "errors": {"address": [msg]},
            },
            status=400,
        )
    except IntegrityError:
        msg = "This wallet address cannot be registered because it conflicts with an existing address."
        return JsonResponse(
            {
                "success": False,
                "error": msg,
                "errors": {"address": [msg]},
            },
            status=400,
        )

    viewer_tz = ZoneInfo(request.user.timezone)
    context = _build_finance_context(tenant, teacher, viewer_tz)
    if address_obj.status == PaymentAddress.Status.ACTIVE:
        message = (
            "Receiving address activated immediately for BNB Smart Chain (BEP-20)."
        )
    else:
        message = (
            "Receiving address change scheduled. It will activate after the 24-hour "
            "security cooling-off window."
        )

    return JsonResponse(
        {
            "success": True,
            "message": message,
            "address": address_obj.address,
            "address_status": address_obj.status,
            "activates_at_iso": (
                address_obj.activates_at.isoformat() if address_obj.activates_at else ""
            ),
            "receiving_address_html": render_to_string(
                "payments/partials/_finance_receiving_address.html",
                context,
                request=request,
            ),
        }
    )


@student_required
@require_POST
@terms_accepted_required
def student_topup_initiate(request):
    """Create or reuse an unexpired PENDING top-up Order for the authenticated student."""
    tenant = get_default_tenant()
    if tenant is None:
        return JsonResponse(
            {"success": False, "error": "No active storefront tenant configured."},
            status=400,
        )

    existing_order = get_active_pending_order(tenant, request.user)
    if existing_order is not None:
        reused = True
        order = existing_order
    else:
        form = StudentTopUpInitiateForm(request.POST, tenant=tenant)
        if not form.is_valid():
            return JsonResponse(
                {
                    "success": False,
                    "error": _first_form_error(form),
                    "errors": form.errors,
                },
                status=400,
            )

        try:
            order = create_topup_order(
                tenant=tenant,
                student=request.user,
                amount=form.cleaned_data["amount"],
                currency=tenant.default_currency,
            )
        except ValidationError as exc:
            msg = _validation_error_message(exc)
            return JsonResponse(
                {"success": False, "error": msg, "errors": {"__all__": [msg]}},
                status=400,
            )
        reused = False

    viewer_tz = ZoneInfo(getattr(request.user, "timezone", "UTC") or "UTC")
    payload = build_topup_order_payload(
        order, tenant, viewer_tz, include_instructions=True
    )
    payload["success"] = True
    payload["reused"] = reused
    return JsonResponse(payload)


def _run_student_order_action(request, order_id: int, action_fn):
    """Resolve tenant & order, run an order mutation, and map domain errors to JSON responses."""
    tenant = get_default_tenant()
    if tenant is None:
        return JsonResponse(
            {"success": False, "error": "No active storefront tenant configured."},
            status=400,
        )

    order = get_object_or_404(Order, pk=order_id)
    try:
        return action_fn(tenant, order)
    except PermissionDenied as exc:
        return JsonResponse(
            {"success": False, "error": str(exc) or "Permission denied."},
            status=403,
        )
    except TopupOrderConflictError as exc:
        return JsonResponse(
            {"success": False, "error": _validation_error_message(exc)},
            status=409,
        )
    except ValidationError as exc:
        msg = _validation_error_message(exc)
        return JsonResponse(
            {"success": False, "error": msg, "errors": {"__all__": [msg]}},
            status=400,
        )


@student_required
@require_POST
@terms_accepted_required
def student_topup_claim(request, order_id: int):
    """Record a student's 'I Have Paid' claim on a pending top-up order."""
    viewer_tz = ZoneInfo(getattr(request.user, "timezone", "UTC") or "UTC")
    form = StudentTopUpClaimForm(request.POST, viewer_tz=viewer_tz)
    if not form.is_valid():
        return JsonResponse(
            {
                "success": False,
                "error": _first_form_error(form),
                "errors": form.errors,
            },
            status=400,
        )

    def perform_claim(tenant: Tenant, order: Order) -> JsonResponse:
        claimed_order = submit_topup_claim(
            tenant=tenant,
            student=request.user,
            order=order,
            txhash=form.cleaned_data["txhash"],
            paid_at=form.cleaned_data["paid_at"],
        )
        return JsonResponse(
            {
                "success": True,
                "order_id": claimed_order.id,
                "claimed_txhash": claimed_order.claimed_txhash,
                "message": (
                    "Your payment claim has been submitted. "
                    "The teacher will verify the deposit and credit your account shortly."
                ),
            }
        )

    return _run_student_order_action(request, order_id, perform_claim)


@student_required
@require_POST
@terms_accepted_required
def student_topup_cancel(request, order_id: int):
    """Cancel an unclaimed pending top-up order so the student can choose a new amount."""

    def perform_cancel(tenant: Tenant, order: Order) -> JsonResponse:
        cancelled_order = cancel_pending_topup_order(
            tenant=tenant,
            student=request.user,
            order=order,
        )
        return JsonResponse(
            {
                "success": True,
                "order_id": cancelled_order.id,
                "status": cancelled_order.status,
            }
        )

    return _run_student_order_action(request, order_id, perform_cancel)


def _render_student_payouts_html(request, tenant: Tenant, user) -> str:
    viewer_tz = ZoneInfo(getattr(user, "timezone", "UTC") or "UTC")
    student_payouts = get_student_payouts_for_display(tenant, user, viewer_tz)
    return render_to_string(
        "wallet/partials/_student_payouts_list.html",
        {"student_payouts": student_payouts},
        request=request,
    )


@student_required
@require_POST
def student_payout_request(request):
    """Stage 1: Validate student refund request, create PENDING_CONFIRMATION payout, and send confirmation email."""
    tenant = get_default_tenant()
    if tenant is None:
        return JsonResponse(
            {"success": False, "error": "No active storefront tenant configured."},
            status=400,
        )

    snapshot = get_student_wallet_snapshot(request.user, request=request)
    if snapshot.is_frozen:
        msg = "Your wallet is currently frozen. New refund requests are paused."
        return JsonResponse(
            {"success": False, "error": msg, "errors": {"__all__": [msg]}},
            status=400,
        )

    form = StudentRefundRequestForm(
        request.POST,
        available_balance=snapshot.available_balance,
    )
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
    try:
        payout = create_payout_request(
            tenant=tenant,
            student=request.user,
            amount_gross=cleaned["amount"],
            destination=cleaned["destination"],
            notes=cleaned["notes"],
        )
    except (PayoutInsufficientBalanceError, WalletFrozenError, ValidationError) as exc:
        msg = _validation_error_message(exc)
        return JsonResponse(
            {"success": False, "error": msg, "errors": {"__all__": [msg]}},
            status=400,
        )

    confirmation_hours = getattr(settings, "PAYOUT_CONFIRMATION_HOURS", 24)
    notice = (
        f"We have sent a confirmation link to your email. "
        f"You must confirm this request within {confirmation_hours} hours."
    )
    return JsonResponse(
        {
            "success": True,
            "payout_id": payout.id,
            "status": payout.status,
            "amount_gross": f"{payout.amount_gross:.2f}",
            "currency": tenant.default_currency,
            "message": notice,
            "payouts_html": _render_student_payouts_html(request, tenant, request.user),
        }
    )


def _render_payout_confirm_result(
    request,
    mode: str,
    *,
    payout: Payout | None = None,
    currency: str = "USD",
    raw_token: str = "",
    error_message: str = "",
    status: int = 200,
):
    return render(
        request,
        "wallet/payout_confirm_result.html",
        {
            "mode": mode,
            "payout": payout,
            "currency": currency,
            "raw_token": raw_token,
            "error_message": error_message,
            "confirmation_hours": getattr(settings, "PAYOUT_CONFIRMATION_HOURS", 24),
        },
        status=status,
    )


@student_required
def student_payout_confirm(request, raw_token: str):
    """
    Stage 2 confirmation landing view:
    - GET: Safe preview without mutating state (protects against email link scanners).
    - POST: Verifies token, locks Wallet -> Payout, places PAYOUT_HOLD, and queues for teacher review.
    """
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    tenant = get_default_tenant()
    if tenant is None:
        return _render_payout_confirm_result(
            request,
            "invalid",
            error_message="No active storefront tenant configured.",
            status=400,
        )

    payout = get_payout_by_raw_token(tenant=tenant, raw_token=raw_token)
    inspection = inspect_payout_confirmation_state(
        tenant=tenant,
        payout=payout,
        user=request.user,
    )
    state = inspection["state"]
    currency = tenant.default_currency

    if request.method == "GET":
        if state == "ready":
            return _render_payout_confirm_result(
                request,
                "preview",
                payout=payout,
                currency=currency,
                raw_token=raw_token,
            )
        if state == "already_confirmed":
            return _render_payout_confirm_result(
                request,
                "already_confirmed",
                payout=payout,
                currency=currency,
            )
        return _render_payout_confirm_result(
            request,
            state,
            payout=payout,
            currency=currency,
            status=400,
        )

    # POST: execute state mutation under row locks
    if payout is None:
        return _render_payout_confirm_result(
            request, "invalid", currency=currency, status=400
        )

    if state == "already_confirmed":
        return _render_payout_confirm_result(
            request, "already_confirmed", payout=payout, currency=currency
        )

    try:
        verify_and_reserve_payout(
            tenant=tenant,
            payout=payout,
            raw_token=raw_token,
        )
    except WalletFrozenError as exc:
        return _render_payout_confirm_result(
            request,
            "frozen",
            payout=payout,
            currency=currency,
            error_message=str(exc),
            status=400,
        )
    except PayoutInsufficientBalanceError as exc:
        return _render_payout_confirm_result(
            request,
            "insufficient_balance",
            payout=payout,
            currency=currency,
            error_message=str(exc),
            status=400,
        )
    except ValidationError as exc:
        payout.refresh_from_db()
        if payout.status in (
            Payout.Status.REQUESTED,
            Payout.Status.PROCESSING,
            Payout.Status.COMPLETED,
        ):
            return _render_payout_confirm_result(
                request, "already_confirmed", payout=payout, currency=currency
            )

        msg = _validation_error_message(exc)
        is_expired = payout.status == Payout.Status.EXPIRED or (
            payout.confirmation_expires_at is not None
            and timezone.now() > payout.confirmation_expires_at
        )
        mode = (
            "expired"
            if is_expired
            else (
                "cancelled"
                if payout.status in (Payout.Status.CANCELLED, Payout.Status.REJECTED)
                else "invalid"
            )
        )
        return _render_payout_confirm_result(
            request,
            mode,
            payout=payout,
            currency=currency,
            error_message=msg,
            status=400,
        )

    return _render_payout_confirm_result(
        request, "confirmed", payout=payout, currency=currency
    )


@student_required
@require_POST
def student_payout_cancel(request, payout_id: int):
    """Allow an authenticated student to cancel their own PENDING_CONFIRMATION or REQUESTED payout."""
    tenant = get_default_tenant()
    if tenant is None:
        return JsonResponse(
            {"success": False, "error": "No active storefront tenant configured."},
            status=400,
        )

    payout = get_object_or_404(
        Payout.objects.select_related("student", "tenant"),
        pk=payout_id,
        tenant=tenant,
    )

    try:
        cancelled_payout = cancel_student_payout_request(
            tenant=tenant,
            payout=payout,
            user=request.user,
        )
    except PermissionDenied as exc:
        if _is_ajax_or_json_request(request):
            return JsonResponse(
                {"success": False, "error": str(exc) or "Permission denied."},
                status=403,
            )
        raise
    except PayoutConflictError as exc:
        return JsonResponse(
            {"success": False, "error": str(exc)},
            status=409,
        )

    if getattr(cancelled_payout, "released_hold", False):
        message = (
            f"Refund request #{cancelled_payout.id} cancelled and "
            f"${cancelled_payout.amount_gross:.2f} {tenant.default_currency} "
            f"released back to your available balance."
        )
    else:
        message = f"Refund request #{cancelled_payout.id} cancelled."

    if not _is_ajax_or_json_request(request):
        messages.success(request, message)
        return redirect("wallet:student_wallet")

    request._student_wallet_snapshot = None
    snapshot = get_student_wallet_snapshot(request.user, request=request)
    return JsonResponse(
        {
            "success": True,
            "payout_id": cancelled_payout.id,
            "status": cancelled_payout.status,
            "message": message,
            "available_balance": f"{snapshot.available_balance:.2f}",
            "reserved_balance": f"{snapshot.reserved_balance:.2f}",
            "settled_balance": f"{snapshot.settled_balance:.2f}",
            "payouts_html": _render_student_payouts_html(request, tenant, request.user),
        }
    )
