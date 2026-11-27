from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from accounts.models import Tenant
from accounts.tests.base import RoleTestCase
from payments.services import create_payout_request, verify_and_reserve_payout
from wallet.models import LedgerEntry
from wallet.services import (
    STUDENT_LEDGER_CATEGORIES,
    classify_student_ledger_entry,
    credit_wallet,
    format_student_ledger_description,
    get_entry_types_for_student_category,
    get_or_create_wallet,
    reserve_funds,
)

User = get_user_model()


class StudentLedgerClassificationTests(SimpleTestCase):
    """Unit tests for mapping all 10 LedgerEntry.EntryType values to 5 categories and net impacts."""

    def test_all_ten_entry_types_map_to_expected_categories_and_net_impacts(self):
        expected = {
            LedgerEntry.EntryType.CREDIT: ("CREDIT", "positive", "+$25.00"),
            LedgerEntry.EntryType.WAIVER: ("CREDIT", "positive", "+$25.00"),
            LedgerEntry.EntryType.ADJUSTMENT_CREDIT: ("CREDIT", "positive", "+$25.00"),
            LedgerEntry.EntryType.RESERVE: ("HOLD", "negative", "-$25.00"),
            LedgerEntry.EntryType.RELEASE: ("RELEASE", "positive", "+$25.00"),
            LedgerEntry.EntryType.CONSUME: ("SETTLEMENT", "neutral", "$25.00"),
            LedgerEntry.EntryType.ADJUSTMENT_DEBIT: (
                "SETTLEMENT",
                "negative",
                "-$25.00",
            ),
            LedgerEntry.EntryType.PAYOUT_HOLD: ("REFUND", "negative", "-$25.00"),
            LedgerEntry.EntryType.PAYOUT_RELEASE: ("REFUND", "positive", "+$25.00"),
            LedgerEntry.EntryType.PAYOUT_SETTLE: ("REFUND", "neutral", "$25.00"),
        }

        self.assertEqual(set(expected.keys()), set(LedgerEntry.EntryType.values))
        self.assertEqual(
            [code for code, _ in STUDENT_LEDGER_CATEGORIES],
            ["CREDIT", "HOLD", "RELEASE", "SETTLEMENT", "REFUND"],
        )

        for entry_type, (category, direction, formatted) in expected.items():
            info = classify_student_ledger_entry(
                entry_type=entry_type,
                amount=Decimal("25.00"),
            )
            self.assertEqual(info.category, category, msg=entry_type)
            self.assertEqual(info.impact_direction, direction, msg=entry_type)
            self.assertEqual(info.formatted_impact, formatted, msg=entry_type)

    def test_get_entry_types_for_student_category_handles_valid_and_invalid_inputs(
        self,
    ):
        self.assertEqual(
            get_entry_types_for_student_category("CREDIT"),
            (
                LedgerEntry.EntryType.CREDIT,
                LedgerEntry.EntryType.WAIVER,
                LedgerEntry.EntryType.ADJUSTMENT_CREDIT,
            ),
        )
        self.assertEqual(
            get_entry_types_for_student_category("hold"),
            (LedgerEntry.EntryType.RESERVE,),
        )
        self.assertIsNone(get_entry_types_for_student_category("INVALID_TYPE"))
        self.assertIsNone(get_entry_types_for_student_category(""))
        self.assertIsNone(get_entry_types_for_student_category(None))

    def test_format_student_ledger_description_hides_internal_audit_tokens(self):
        cases = [
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.RESERVE,
                    reference_id="booking_150",
                    description="Booking hold: booking #150",
                ),
                "Lesson Hold for Booking #150",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.RESERVE,
                    reference_id="misc_ref",
                    description="Booking hold",
                ),
                "Lesson Hold",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.RELEASE,
                    reference_id="booking_147",
                    description="Booking settlement (release: STUDENT_CANCELLED) by user:12 for booking #147",
                ),
                "Lesson Hold Released for Booking #147",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.RELEASE,
                    reference_id="booking_148",
                    description="Booking settlement (release: DISPUTE_STUDENT_FAVOR) by user:10 for booking #148",
                ),
                "Lesson Hold Released for Booking #148 (Dispute Resolved)",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.CONSUME,
                    reference_id="booking_146",
                    description="Booking settlement (consume: COMPLETED) by system for booking #146",
                ),
                "Lesson Settlement for Booking #146",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.CONSUME,
                    reference_id="booking_145",
                    description="Booking settlement (consume: DISPUTE_TEACHER_FAVOR) by user:10 for booking #145",
                ),
                "Lesson Settlement for Booking #145 (Dispute Resolved)",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.CREDIT,
                    reference_id="tenant_1:manual_REF999",
                    description="Manual top-up: tenant_1:manual_REF999",
                ),
                "Wallet Deposit Credit",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.CREDIT,
                    reference_id="ref_custom",
                    description="Manual Deposit Credit",
                ),
                "Manual Deposit Credit",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.WAIVER,
                    reference_id="booking_149",
                    description="Penalty waiver for booking #149: Goodwill fee waiver",
                ),
                "Penalty Waiver Credit for Booking #149",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.ADJUSTMENT_CREDIT,
                    reference_id="admin_1",
                    description="Administrative adjustment (credit): internal ops note",
                ),
                "Account Balance Adjustment",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.ADJUSTMENT_DEBIT,
                    reference_id="admin_1",
                    description="Administrative adjustment (debit): internal ops note",
                ),
                "Account Balance Adjustment",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.PAYOUT_HOLD,
                    reference_id="payout_5",
                    description="Payout hold: payout #5",
                ),
                "Refund Request Hold",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.PAYOUT_RELEASE,
                    reference_id="payout_5",
                    description="Payout rejected (Invalid address): payout #5",
                ),
                "Refund Hold Released",
            ),
            (
                LedgerEntry(
                    entry_type=LedgerEntry.EntryType.PAYOUT_SETTLE,
                    reference_id="0xabc123",
                    description="Payout settled: 0xabc123 for payout #5",
                ),
                "Refund Completed",
            ),
        ]

        for entry, expected_desc in cases:
            rendered = format_student_ledger_description(entry)
            self.assertEqual(rendered, expected_desc, msg=entry.description)
            for forbidden in (
                "by user:",
                "DISPUTE_",
                "tenant_1:",
                "(consume:",
                "(release:",
            ):
                self.assertNotIn(forbidden, rendered)


class NavbarWalletBadgeTests(RoleTestCase):
    """Tests for student_wallet_context short-circuiting and navbar balance badge rendering."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

        cls.other_teacher_user = User.objects.create_user(
            username="other_wallet_nav_teacher",
            email="other_wallet_nav_teacher@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        cls.other_tenant = Tenant.objects.get(
            teacher_profile=cls.other_teacher_user.teacher_profile
        )

    def test_context_processor_short_circuits_with_zero_queries_for_anon_and_teacher(
        self,
    ):
        from wallet.context_processors import student_wallet_context

        factory = RequestFactory()
        anon_req = factory.get("/")
        anon_req.user = AnonymousUser()
        with self.assertNumQueries(0):
            self.assertEqual(
                student_wallet_context(anon_req),
                {"student_wallet_summary": None},
            )

        teacher_req = factory.get("/dashboard/teacher/")
        teacher_req.user = self.teacher_user
        with self.assertNumQueries(0):
            self.assertEqual(
                student_wallet_context(teacher_req),
                {"student_wallet_summary": None},
            )

    def test_anonymous_visitor_does_not_see_wallet_badge(self):
        response = self.anon_client.get(reverse("portfolio:landing_page"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="navbarWalletBadge"')
        self.assertNotContains(response, 'href="/wallet/"')

    def test_teacher_does_not_see_wallet_badge(self):
        response = self.teacher_client.get(reverse("booking:teacher_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="navbarWalletBadge"')
        self.assertNotContains(response, 'href="/wallet/"')

    def test_student_balance_badge_displays_available_and_breakdown_popover(self):
        wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("75.00"),
            idempotency_key="nav-badge-credit-75",
            reference_id="ref_nav_75",
            description="Top-up deposit",
        )
        reserve_funds(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("25.00"),
            idempotency_key="nav-badge-reserve-25",
            reference_id="booking_nav_25",
            description="Lesson hold",
        )

        # Other tenant wallet must not leak into student's navbar summary
        other_wallet = get_or_create_wallet(self.other_tenant, self.student_user)
        credit_wallet(
            tenant=self.other_tenant,
            wallet=other_wallet,
            amount=Decimal("400.00"),
            idempotency_key="nav-badge-other-400",
            reference_id="ref_other_400",
            description="Other tenant credit",
        )

        response = self.student_client.get(reverse("booking:student_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="navbarWalletBadge"')
        self.assertContains(response, 'href="/wallet/"')
        self.assertContains(response, "$50.00")
        self.assertContains(response, "Available Balance: <strong>$50.00</strong>")
        self.assertContains(
            response, "Reserved in Lesson Holds: <strong>$25.00</strong>"
        )
        self.assertContains(response, "Settled Account Total: <strong>$75.00</strong>")
        self.assertNotContains(response, "400.00")

    def test_student_without_wallet_row_shows_zero_state_without_creating_wallet(self):
        response = self.student_client.get(reverse("booking:student_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="navbarWalletBadge"')
        self.assertContains(response, "$0.00")
        self.assertFalse(self.student_user.wallets.filter(tenant=self.tenant).exists())


class StudentWalletDashboardViewTests(RoleTestCase):
    """Tests for GET /wallet/ permissions, summary cards, and CTA modal shells."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

    def test_wallet_page_requires_authenticated_student(self):
        anon_resp = self.anon_client.get("/wallet/")
        self.assertEqual(anon_resp.status_code, 302)
        self.assertIn("/accounts/login/", anon_resp.url)

        teacher_resp = self.teacher_client.get("/wallet/")
        self.assertEqual(teacher_resp.status_code, 403)

        student_resp = self.student_client.get("/wallet/")
        self.assertEqual(student_resp.status_code, 200)
        self.assertEqual(reverse("wallet:student_wallet"), "/wallet/")

    def test_wallet_page_displays_summary_cards_and_cta_modal_hooks(self):
        wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("75.00"),
            idempotency_key="dash-credit-75",
            reference_id="ref_dash_75",
            description="Manual Deposit Credit",
        )
        reserve_funds(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("25.00"),
            idempotency_key="dash-reserve-25",
            reference_id="booking_124",
            description="Lesson Hold for Booking #124",
        )

        response = self.student_client.get(reverse("wallet:student_wallet"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Available Balance")
        self.assertContains(response, "Reserved in Lesson Holds")
        self.assertContains(response, "Settled Account Total")
        self.assertContains(response, "$50.00")
        self.assertContains(response, "$25.00")
        self.assertContains(response, "$75.00")

        self.assertContains(response, "Add Funds")
        self.assertContains(response, 'data-bs-target="#topUpModal"')
        self.assertContains(response, 'id="topUpModal"')

        self.assertContains(response, "Request Refund")
        self.assertContains(response, 'data-bs-target="#refundModal"')
        self.assertContains(response, 'id="refundModal"')


class StudentWalletLedgerTableTests(RoleTestCase):
    """Tests for ledger listing, ordering, category filtering, pagination, and isolation."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tenant = Tenant.objects.get(teacher_profile=cls.teacher)

        cls.other_student = User.objects.create_user(
            username="other_wallet_student",
            email="other_wallet_student@example.com",
            password="password123",
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        cls.other_teacher = User.objects.create_user(
            username="other_wallet_ledger_teacher",
            email="other_wallet_ledger_teacher@example.com",
            password="password123",
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        cls.other_tenant = Tenant.objects.get(
            teacher_profile=cls.other_teacher.teacher_profile
        )

    def test_ledger_table_lists_entries_reverse_chronologically_with_badges_and_impacts(
        self,
    ):
        wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_entry = credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("100.00"),
            idempotency_key="ledger-test-credit-100",
            reference_id="ref_credit_100",
            description="Manual Deposit Credit",
        )
        reserve_entry = reserve_funds(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("30.00"),
            idempotency_key="ledger-test-reserve-30",
            reference_id="booking_124",
            description="Lesson Hold for Booking #124",
        )
        release_entry = LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=wallet,
            entry_type=LedgerEntry.EntryType.RELEASE,
            amount=Decimal("30.00"),
            settled_after=Decimal("100.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="ledger-test-release-30",
            reference_id="booking_124",
            description="Lesson Hold Released for Booking #124",
        )
        consume_entry = LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=wallet,
            entry_type=LedgerEntry.EntryType.CONSUME,
            amount=Decimal("25.00"),
            settled_after=Decimal("75.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="ledger-test-consume-25",
            reference_id="booking_125",
            description="Lesson Settlement for Booking #125",
        )
        payout = create_payout_request(
            tenant=self.tenant,
            student=self.student_user,
            amount_gross=Decimal("15.00"),
            destination="0x000000000000000000000000000000000000dEaD",
        )
        payout_hold_entry = verify_and_reserve_payout(
            tenant=self.tenant,
            payout=payout,
            raw_token=payout.raw_token,
        )

        # Verify ordering from view matches -created_at, -id
        response = self.student_client.get(reverse("wallet:student_wallet"))
        self.assertEqual(response.status_code, 200)

        page_entries = list(response.context["ledger_entries"])
        self.assertEqual(
            [e.id for e in page_entries],
            [
                payout_hold_entry.id,
                consume_entry.id,
                release_entry.id,
                reserve_entry.id,
                credit_entry.id,
            ],
        )

        self.assertContains(response, "Manual Deposit Credit")
        self.assertContains(response, "Lesson Hold for Booking #124")
        self.assertContains(response, "Lesson Hold Released for Booking #124")
        self.assertContains(response, "Lesson Settlement for Booking #125")
        self.assertContains(response, "+$100.00")
        self.assertContains(response, "-$30.00")
        self.assertContains(response, "+$30.00")
        self.assertContains(response, "-$15.00")
        self.assertContains(response, "Credit")
        self.assertContains(response, "Hold")
        self.assertContains(response, "Release")
        self.assertContains(response, "Settlement")
        self.assertContains(response, "Refund")

    def test_ledger_pagination_at_20_entries_per_page_with_no_duplicates(self):
        wallet = get_or_create_wallet(self.tenant, self.student_user)
        created_ids = []
        for idx in range(25):
            entry = credit_wallet(
                tenant=self.tenant,
                wallet=wallet,
                amount=Decimal("10.00"),
                idempotency_key=f"page-credit-{idx}",
                reference_id=f"ref_page_{idx:02d}",
                description=f"Deposit Batch #{idx:02d}",
            )
            created_ids.append(entry.id)

        page1 = self.student_client.get(reverse("wallet:student_wallet"))
        self.assertEqual(page1.status_code, 200)
        page1_ids = [e.id for e in page1.context["ledger_entries"]]
        self.assertEqual(len(page1_ids), 20)
        self.assertContains(page1, "Deposit Batch #24")
        self.assertNotContains(page1, "Deposit Batch #00")

        page2 = self.student_client.get(reverse("wallet:student_wallet"), {"page": 2})
        self.assertEqual(page2.status_code, 200)
        page2_ids = [e.id for e in page2.context["ledger_entries"]]
        self.assertEqual(len(page2_ids), 5)
        self.assertContains(page2, "Deposit Batch #00")
        self.assertNotContains(page2, "Deposit Batch #24")

        # No duplicates and exact full coverage in reverse order
        self.assertEqual(len(set(page1_ids) & set(page2_ids)), 0)
        self.assertEqual(page1_ids + page2_ids, list(reversed(created_ids)))

    def test_ledger_type_filtering_and_invalid_type_fallback(self):
        wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("80.00"),
            idempotency_key="filter-credit-80",
            reference_id="ref_filter_80",
            description="Filter Credit Entry",
        )
        reserve_funds(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("20.00"),
            idempotency_key="filter-reserve-20",
            reference_id="booking_20",
            description="Booking hold: booking #20",
        )

        hold_resp = self.student_client.get(
            reverse("wallet:student_wallet"), {"type": "HOLD"}
        )
        self.assertEqual(hold_resp.status_code, 200)
        self.assertContains(hold_resp, "Lesson Hold for Booking #20")
        self.assertNotContains(hold_resp, "Filter Credit Entry")
        self.assertEqual(hold_resp.context["active_type_filter"], "HOLD")

        invalid_resp = self.student_client.get(
            reverse("wallet:student_wallet"), {"type": "BOGUS_TYPE"}
        )
        self.assertEqual(invalid_resp.status_code, 200)
        self.assertContains(invalid_resp, "Lesson Hold for Booking #20")
        self.assertContains(invalid_resp, "Filter Credit Entry")
        self.assertEqual(invalid_resp.context["active_type_filter"], "")

    def test_ledger_isolates_other_students_and_other_tenants(self):
        student_wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=student_wallet,
            amount=Decimal("40.00"),
            idempotency_key="iso-own-40",
            reference_id="ref_iso_own",
            description="Own Student Entry",
        )

        other_student_wallet = get_or_create_wallet(self.tenant, self.other_student)
        credit_wallet(
            tenant=self.tenant,
            wallet=other_student_wallet,
            amount=Decimal("99.00"),
            idempotency_key="iso-other-student-99",
            reference_id="ref_iso_other_student",
            description="Other Student Secret Entry",
        )

        other_tenant_wallet = get_or_create_wallet(self.other_tenant, self.student_user)
        credit_wallet(
            tenant=self.other_tenant,
            wallet=other_tenant_wallet,
            amount=Decimal("123.00"),
            idempotency_key="iso-other-tenant-123",
            reference_id="ref_iso_other_tenant",
            description="Other Tenant Secret Entry",
        )

        response = self.student_client.get(reverse("wallet:student_wallet"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Own Student Entry")
        self.assertContains(response, "$40.00")
        self.assertNotContains(response, "Other Student Secret Entry")
        self.assertNotContains(response, "99.00")
        self.assertNotContains(response, "Other Tenant Secret Entry")
        self.assertNotContains(response, "123.00")

    def test_wallet_view_renders_humanized_descriptions_instead_of_raw_audit_strings(
        self,
    ):
        wallet = get_or_create_wallet(self.tenant, self.student_user)
        credit_wallet(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("60.00"),
            idempotency_key="raw-desc-credit",
            reference_id="tenant_1:manual_XYZ",
            description="Manual top-up: tenant_1:manual_XYZ",
        )
        reserve_funds(
            tenant=self.tenant,
            wallet=wallet,
            amount=Decimal("25.00"),
            idempotency_key="raw-desc-reserve",
            reference_id="booking_150",
            description="Booking hold: booking #150",
        )
        LedgerEntry.objects.create(
            tenant=self.tenant,
            wallet=wallet,
            entry_type=LedgerEntry.EntryType.CONSUME,
            amount=Decimal("25.00"),
            settled_after=Decimal("35.00"),
            reserved_after=Decimal("0.00"),
            idempotency_key="raw-desc-consume",
            reference_id="booking_145",
            description="Booking settlement (consume: DISPUTE_TEACHER_FAVOR) by user:10 for booking #145",
        )

        response = self.student_client.get(reverse("wallet:student_wallet"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wallet Deposit Credit")
        self.assertContains(response, "Lesson Hold for Booking #150")
        self.assertContains(
            response, "Lesson Settlement for Booking #145 (Dispute Resolved)"
        )
        self.assertNotContains(response, "DISPUTE_TEACHER_FAVOR")
        self.assertNotContains(response, "by user:10")
        self.assertNotContains(response, "tenant_1:manual_XYZ")
