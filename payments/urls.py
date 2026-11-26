from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.teacher_finance_dashboard, name="teacher_finance"),
    path("manual-topup/", views.manual_topup, name="manual_topup"),
    path(
        "payouts/<int:payout_id>/processing/",
        views.payout_mark_processing,
        name="payout_mark_processing",
    ),
    path(
        "payouts/<int:payout_id>/complete/",
        views.payout_complete,
        name="payout_complete",
    ),
    path(
        "payouts/<int:payout_id>/reject/",
        views.payout_reject,
        name="payout_reject",
    ),
]
