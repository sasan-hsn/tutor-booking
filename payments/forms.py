from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from accounts.models import get_default_min_topup_amount
from booking.models import Booking
from payments.models import Payment
from payments.services import (
    DUPLICATE_PAYOUT_EXTERNAL_REF_ERROR,
    build_manual_external_id,
    get_user_display_name,
    has_duplicate_payout_external_ref,
)

User = get_user_model()

DUPLICATE_REFERENCE_ERROR = (
    "A manual deposit with this receipt reference has already been credited."
)
DUPLICATE_IDEMPOTENCY_ERROR = (
    "This submission token has already been used for another deposit."
)


class StudentChoiceField(forms.ModelChoiceField):
    """Displays student full name (or username) alongside email in select options."""

    def label_from_instance(self, obj):
        display = get_user_display_name(obj)
        if obj.email:
            return f"{display} ({obj.email})"
        return display


class ManualTopUpForm(forms.Form):
    """Validates teacher-initiated manual top-up submissions."""

    student = StudentChoiceField(
        queryset=User.objects.none(),
        empty_label="Select a student...",
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "id": "manualTopupStudent",
                "required": True,
            }
        ),
    )
    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "id": "manualTopupAmount",
                "step": "0.01",
                "placeholder": "20.00",
                "required": True,
            }
        ),
    )
    reference_id = forms.CharField(
        max_length=128,
        required=True,
        label="Receipt / Transaction Reference",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "id": "manualTopupReference",
                "placeholder": "e.g. TXHASH or bank receipt reference",
                "required": True,
            }
        ),
    )
    notes = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "id": "manualTopupNotes",
                "rows": 2,
                "placeholder": "Optional verification notes",
            }
        ),
    )
    idempotency_key = forms.CharField(
        required=False,
        max_length=128,
        widget=forms.HiddenInput(attrs={"id": "manualTopupIdempotencyKey"}),
    )

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.fields["student"].queryset = User.objects.filter(
            role=User.Role.STUDENT,
            is_active=True,
        ).order_by("first_name", "last_name", "username")
        self.fields["amount"].widget.attrs["min"] = f"{self._min_topup_amount():.2f}"

    def _min_topup_amount(self) -> Decimal:
        if self.tenant is not None:
            return self.tenant.min_topup_amount
        return get_default_min_topup_amount()

    def clean_amount(self) -> Decimal:
        amount = self.cleaned_data["amount"]
        min_amount = self._min_topup_amount()
        currency = self.tenant.default_currency if self.tenant is not None else "USD"
        if amount <= Decimal("0.00"):
            raise ValidationError("Amount must be greater than zero.")
        if amount < min_amount:
            raise ValidationError(
                f"Amount must be at least ${min_amount:.2f} {currency}."
            )
        return amount

    def clean_reference_id(self) -> str:
        ref = (self.cleaned_data.get("reference_id") or "").strip()
        if not ref:
            raise ValidationError("Receipt / Transaction Reference is required.")
        return ref

    def clean(self):
        cleaned_data = super().clean()
        clean_ref = cleaned_data.get("reference_id")
        idem_key = (cleaned_data.get("idempotency_key") or "").strip()
        cleaned_data["idempotency_key"] = idem_key

        if clean_ref and self.tenant is not None:
            external_id = build_manual_external_id(self.tenant.id, clean_ref)
            existing = Payment.objects.filter(external_id=external_id).first()
            if existing is not None:
                existing_idem = (existing.raw_metadata or {}).get("idempotency_key")
                if not idem_key or existing_idem != idem_key:
                    self.add_error("reference_id", DUPLICATE_REFERENCE_ERROR)
            elif idem_key:
                conflicting = Payment.objects.filter(
                    tenant=self.tenant,
                    raw_metadata__idempotency_key=idem_key,
                ).first()
                if conflicting is not None:
                    self.add_error("reference_id", DUPLICATE_IDEMPOTENCY_ERROR)

        return cleaned_data


class CompletePayoutForm(forms.Form):
    """Validates external transaction hash / reference when completing a payout."""

    txhash = forms.CharField(
        max_length=128,
        required=True,
        label="Transaction Hash / External Reference",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "id": "completePayoutTxhash",
                "placeholder": "e.g. 0x... transaction hash or reference ID",
                "required": True,
            }
        ),
    )

    def __init__(self, *args, payout=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.payout = payout

    def clean_txhash(self) -> str:
        txhash = (self.cleaned_data.get("txhash") or "").strip()
        if not txhash:
            raise ValidationError("Transaction hash / external reference is required.")

        if self.payout is not None and has_duplicate_payout_external_ref(
            provider=self.payout.provider,
            external_ref=txhash,
            exclude_payout_id=self.payout.pk,
        ):
            raise ValidationError(DUPLICATE_PAYOUT_EXTERNAL_REF_ERROR)

        return txhash


class RejectPayoutForm(forms.Form):
    """Validates mandatory rejection reason when rejecting a payout."""

    # Bounded at 200 chars so the formatted LedgerEntry.description stays within 255 chars.
    reason = forms.CharField(
        max_length=200,
        required=True,
        label="Rejection Reason",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "id": "rejectPayoutReason",
                "rows": 3,
                "maxlength": 200,
                "placeholder": "Explain why this refund request is being rejected...",
                "required": True,
            }
        ),
    )

    def clean_reason(self) -> str:
        reason = (self.cleaned_data.get("reason") or "").strip()
        if not reason:
            raise ValidationError("Rejection reason is required.")
        return reason


class ResolveDisputeForm(forms.Form):
    """Validates teacher dispute resolution decision."""

    ALLOWED_RESOLUTIONS = (
        Booking.DisputeResolution.STUDENT_FAVOR,
        Booking.DisputeResolution.TEACHER_FAVOR,
    )

    resolution = forms.ChoiceField(
        choices=[
            (Booking.DisputeResolution.STUDENT_FAVOR, "Resolve in Student Favor"),
            (Booking.DisputeResolution.TEACHER_FAVOR, "Resolve in Teacher Favor"),
        ],
        required=True,
        widget=forms.HiddenInput(attrs={"id": "resolveDisputeResolution"}),
    )

    def clean_resolution(self) -> str:
        resolution = (self.cleaned_data.get("resolution") or "").strip()
        if resolution not in self.ALLOWED_RESOLUTIONS:
            raise ValidationError("Resolution must be STUDENT_FAVOR or TEACHER_FAVOR.")
        return resolution
