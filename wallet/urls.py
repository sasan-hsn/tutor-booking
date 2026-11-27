from django.urls import path

from payments import views as payment_views

from . import views

app_name = "wallet"

urlpatterns = [
    path("", views.student_wallet, name="student_wallet"),
    path(
        "topup/initiate/",
        payment_views.student_topup_initiate,
        name="student_topup_initiate",
    ),
    path(
        "topup/<int:order_id>/claim/",
        payment_views.student_topup_claim,
        name="student_topup_claim",
    ),
    path(
        "topup/<int:order_id>/cancel/",
        payment_views.student_topup_cancel,
        name="student_topup_cancel",
    ),
    path(
        "payouts/request/",
        payment_views.student_payout_request,
        name="student_payout_request",
    ),
    path(
        "payouts/confirm/<str:raw_token>/",
        payment_views.student_payout_confirm,
        name="student_payout_confirm",
    ),
    path(
        "payouts/<int:payout_id>/cancel/",
        payment_views.student_payout_cancel,
        name="student_payout_cancel",
    ),
]
