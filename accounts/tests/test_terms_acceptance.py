from datetime import time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from django.views import View

from accounts.models import Tenant, TermsAcceptance
from accounts.tests.base import RoleTestCase
from booking.models import Booking, RegularAvailability
from wallet.services import credit_wallet, get_or_create_wallet


class AcceptTermsEndpointTests(RoleTestCase):
    """Tests for POST /accounts/terms/accept/ audit trail recording."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

    def test_anonymous_user_redirected_from_accept_terms(self):
        response = self.anon_client.post(
            reverse("accounts:accept_terms"),
            {"accepted": "true"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
        self.assertEqual(TermsAcceptance.objects.count(), 0)

    def test_teacher_forbidden_on_accept_terms(self):
        response = self.teacher_client.post(
            reverse("accounts:accept_terms"),
            {"accepted": "true"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(TermsAcceptance.objects.count(), 0)

    def test_accept_terms_requires_explicit_confirmation(self):
        response = self.student_client.post(
            reverse("accounts:accept_terms"),
            {"accepted": "false"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        self.assertEqual(TermsAcceptance.objects.count(), 0)

    def test_accept_terms_records_audit_trail(self):
        response = self.student_client.post(
            reverse("accounts:accept_terms"),
            {"accepted": "true"},
            HTTP_X_FORWARDED_FOR="203.0.113.42, 10.0.0.1",
            HTTP_USER_AGENT="Mozilla/5.0 TestBrowser/1.0",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["version"], settings.TERMS_CURRENT_VERSION)

        record = TermsAcceptance.objects.get(
            user=self.student_user,
            tenant=self.tenant,
            version=settings.TERMS_CURRENT_VERSION,
        )
        self.assertEqual(record.ip_address, "203.0.113.42")
        self.assertEqual(record.user_agent, "Mozilla/5.0 TestBrowser/1.0")
        self.assertIsNotNone(record.accepted_at)

    def test_accept_terms_is_idempotent_for_same_version(self):
        url = reverse("accounts:accept_terms")
        first = self.student_client.post(
            url,
            {"accepted": "true"},
            HTTP_USER_AGENT="AgentOne",
        )
        second = self.student_client.post(
            url,
            {"accepted": "true"},
            HTTP_USER_AGENT="AgentTwo",
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            TermsAcceptance.objects.filter(
                user=self.student_user,
                tenant=self.tenant,
                version=settings.TERMS_CURRENT_VERSION,
            ).count(),
            1,
        )

    @override_settings(TERMS_CURRENT_VERSION="2026.2")
    def test_accept_terms_after_version_bump_creates_new_version_record(self):
        TermsAcceptance.objects.create(
            user=self.student_user,
            tenant=self.tenant,
            version="2026.1",
            ip_address="198.51.100.1",
            user_agent="OldAgent",
        )
        response = self.student_client.post(
            reverse("accounts:accept_terms"),
            {"accepted": "true", "version": "2026.2"},
            HTTP_USER_AGENT="NewAgent/2.0",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], "2026.2")
        self.assertEqual(
            TermsAcceptance.objects.filter(
                user=self.student_user, tenant=self.tenant
            ).count(),
            2,
        )

    @override_settings(TERMS_CURRENT_VERSION="2026.2")
    def test_accept_terms_rejects_stale_submitted_version_with_409(self):
        response = self.student_client.post(
            reverse("accounts:accept_terms"),
            {"accepted": "true", "version": "2026.1"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["current_version"], "2026.2")
        self.assertEqual(TermsAcceptance.objects.count(), 0)


class TermsGateDecoratorAndMixinTests(RoleTestCase):
    """Tests for terms_accepted_required decorator and TermsAcceptedRequiredMixin."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()

    def test_decorator_raises_permission_denied_until_accepted(self):
        from accounts.decorators import terms_accepted_required

        @terms_accepted_required
        def protected_view(request):
            return HttpResponse("ok")

        request = self.factory.get("/wallet/topup/")
        request.user = self.student_user

        with self.assertRaises(PermissionDenied):
            protected_view(request)

        TermsAcceptance.objects.create(
            user=self.student_user,
            tenant=self.tenant,
            version=settings.TERMS_CURRENT_VERSION,
            ip_address="127.0.0.1",
            user_agent="TestAgent",
        )
        response = protected_view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_mixin_enforces_terms_and_re_gates_on_version_bump(self):
        from accounts.decorators import TermsAcceptedRequiredMixin

        class ProtectedCBV(TermsAcceptedRequiredMixin, View):
            def get(self, request):
                return HttpResponse("cbv-ok")

        view = ProtectedCBV.as_view()
        request = self.factory.get("/wallet/payouts/")
        request.user = self.student_user

        with self.assertRaises(PermissionDenied):
            view(request)

        TermsAcceptance.objects.create(
            user=self.student_user,
            tenant=self.tenant,
            version=settings.TERMS_CURRENT_VERSION,
            ip_address="127.0.0.1",
            user_agent="TestAgent",
        )
        self.assertEqual(view(request).status_code, 200)

        with override_settings(TERMS_CURRENT_VERSION="2026.2"):
            with self.assertRaises(PermissionDenied):
                view(request)


class BookSlotTermsGateTests(RoleTestCase):
    """Tests for booking:book_slot terms acceptance gating when payments are enabled."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.teacher.lesson_price = Decimal("25.00")
        cls.teacher.lesson_price_25 = Decimal("15.00")
        cls.teacher.offers_trial = True
        cls.teacher.trial_price = Decimal("0.00")
        cls.teacher.save()
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

    def setUp(self):
        super().setUp()
        self.tenant.payments_enabled = True
        self.tenant.save(update_fields=["payments_enabled"])

        self.tz = ZoneInfo(self.teacher_user.timezone)
        self.future_day = timezone.localdate() + timedelta(days=2)
        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=self.future_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        self.slot_start = timezone.datetime.combine(
            self.future_day, time(10, 0), tzinfo=self.tz
        )
        wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("50.00"),
            idempotency_key=f"terms-gate-seed-{self.student_user.id}",
            reference_id="terms_gate_seed",
            description="Seed funds for terms gate tests",
        )
        self.book_url = reverse("booking:book_slot")

    def test_direct_post_to_book_slot_without_terms_returns_403_and_raises_permission_denied(
        self,
    ):
        response = self.student_client.post(
            self.book_url,
            {"start_at": self.slot_start.isoformat(), "lesson_option": "regular_50"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Booking.objects.count(), 0)

        from booking.views import book_slot

        rf = RequestFactory()
        direct_req = rf.post(
            self.book_url,
            {"start_at": self.slot_start.isoformat(), "lesson_option": "regular_50"},
        )
        direct_req.user = self.student_user
        with self.assertRaises(PermissionDenied):
            book_slot(direct_req)

    def test_ajax_post_to_book_slot_without_terms_returns_403_json(self):
        response = self.student_client.post(
            self.book_url,
            {"start_at": self.slot_start.isoformat(), "lesson_option": "regular_50"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            {
                "error": "terms_not_accepted",
                "terms_version": settings.TERMS_CURRENT_VERSION,
            },
        )
        self.assertEqual(Booking.objects.count(), 0)

    def test_book_slot_when_payments_disabled_does_not_require_terms(self):
        self.tenant.payments_enabled = False
        self.tenant.save(update_fields=["payments_enabled"])

        response = self.student_client.post(
            self.book_url,
            {"start_at": self.slot_start.isoformat(), "lesson_option": "regular_50"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(Booking.objects.count(), 1)

    def test_immediate_reentry_after_accepting_terms_allows_booking(self):
        blocked = self.student_client.post(
            self.book_url,
            {"start_at": self.slot_start.isoformat(), "lesson_option": "regular_50"},
        )
        self.assertEqual(blocked.status_code, 403)

        accept_res = self.student_client.post(
            reverse("accounts:accept_terms"),
            {"accepted": "true"},
            HTTP_USER_AGENT="GateTestAgent",
        )
        self.assertEqual(accept_res.status_code, 200)

        allowed = self.student_client.post(
            self.book_url,
            {"start_at": self.slot_start.isoformat(), "lesson_option": "regular_50"},
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(allowed.json()["success"])
        self.assertEqual(Booking.objects.count(), 1)

    def test_version_bump_re_gates_previously_accepted_student(self):
        TermsAcceptance.objects.create(
            user=self.student_user,
            tenant=self.tenant,
            version=settings.TERMS_CURRENT_VERSION,
            ip_address="127.0.0.1",
            user_agent="GateTestAgent",
        )
        with override_settings(TERMS_CURRENT_VERSION="2026.2"):
            response = self.student_client.post(
                self.book_url,
                {
                    "start_at": self.slot_start.isoformat(),
                    "lesson_option": "regular_50",
                },
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(Booking.objects.count(), 0)


class StudentBookingTermsModalRenderTests(RoleTestCase):
    """Tests for TermsAcceptanceModal rendering and version context on student_booking."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

    def test_student_booking_renders_terms_modal_with_flags_for_unaccepted_student(
        self,
    ):
        self.tenant.payments_enabled = True
        self.tenant.save(update_fields=["payments_enabled"])

        response = self.student_client.get(reverse("booking:student_booking"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        self.assertIn('id="termsAcceptanceModal"', html)
        self.assertIn('id="termsDocumentBody"', html)
        self.assertIn("Prepaid Balance Nature", html)
        self.assertIn("24-Hour Cancellation Policy", html)
        self.assertIn('id="acceptTermsCheckbox"', html)
        self.assertIn('id="acceptTermsContinueBtn"', html)
        self.assertIn(
            f"I have read, understood, and accept the Terms of Service and Financial Policy (Version {settings.TERMS_CURRENT_VERSION}).",
            html,
        )
        self.assertIn(f'data-accept-url="{reverse("accounts:accept_terms")}"', html)
        self.assertIn('data-terms-required="true"', html)
        self.assertIn('data-terms-accepted="false"', html)

    def test_student_booking_marks_terms_accepted_and_re_flags_on_version_bump(self):
        self.tenant.payments_enabled = True
        self.tenant.save(update_fields=["payments_enabled"])
        TermsAcceptance.objects.create(
            user=self.student_user,
            tenant=self.tenant,
            version=settings.TERMS_CURRENT_VERSION,
            ip_address="127.0.0.1",
            user_agent="RenderTestAgent",
        )

        accepted_res = self.student_client.get(reverse("booking:student_booking"))
        self.assertEqual(accepted_res.status_code, 200)
        self.assertIn('data-terms-accepted="true"', accepted_res.content.decode())

        with override_settings(TERMS_CURRENT_VERSION="2026.2"):
            bumped_res = self.student_client.get(reverse("booking:student_booking"))
            self.assertEqual(bumped_res.status_code, 200)
            bumped_html = bumped_res.content.decode()
            self.assertIn('data-terms-accepted="false"', bumped_html)
            self.assertIn("(Version 2026.2).", bumped_html)

    def test_student_booking_sets_terms_required_false_when_payments_disabled(self):
        self.tenant.payments_enabled = False
        self.tenant.save(update_fields=["payments_enabled"])

        response = self.student_client.get(reverse("booking:student_booking"))
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-terms-required="false"', response.content.decode())
