from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings
from django.urls import reverse

from accounts.models import StudentProfile, Tenant
from payments.models import Payout
from payments.services import (
    create_payout_request,
    mark_payout_processing,
    settle_payout,
    verify_and_reserve_payout,
)
from portfolio.models import TeacherProfile
from wallet.models import LedgerEntry
from wallet.services import credit_wallet, get_or_create_wallet

User = get_user_model()

DEMO_COMPLETED_TXHASH = "0xexisting_demo_txhash_294"
DEMO_STUDENT_PASSWORD = "demo-password-123"

DEMO_STUDENTS = (
    {
        "username": "demo_student_alice",
        "email": "alice.demo@example.com",
        "first_name": "Alice",
        "last_name": "Mercer",
        "seed_amount": Decimal("150.00"),
    },
    {
        "username": "demo_student_bob",
        "email": "bob.demo@example.com",
        "first_name": "Bob",
        "last_name": "Keller",
        "seed_amount": Decimal("120.00"),
    },
    {
        "username": "demo_student_clara",
        "email": "clara.demo@example.com",
        "first_name": "Clara",
        "last_name": "Vance",
        "seed_amount": Decimal("100.00"),
    },
)

SCENARIO_DESTINATIONS = {
    "requested": "0xDemoAliceRequested000000000000000000000001",
    "processing": "0xDemoBobProcessing000000000000000000000002",
    "completed": "0xDemoAliceCompleted000000000000000000000003",
    "pending_confirmation": "0xDemoClaraUnconfirmed00000000000000000004",
}


class Command(BaseCommand):
    help = (
        "Idempotently seeds demo students, ledger-backed wallets, and payout scenarios "
        "for local QA of the Teacher Dashboard Finance Payout Review Queue."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--teacher",
            type=str,
            default="",
            help="Optional username of the target teacher (defaults to first teacher).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow running even when settings.DEBUG is False.",
        )

    def _resolve_teacher_and_tenant(self, teacher_username: str):
        qs = TeacherProfile.objects.select_related("user").order_by("id")
        if teacher_username:
            teacher = qs.filter(user__username=teacher_username).first()
            if teacher is None:
                raise CommandError(
                    f"Teacher with username {teacher_username!r} was not found."
                )
        else:
            teacher = qs.first()
            if teacher is None:
                raise CommandError(
                    "No TeacherProfile exists in the database. Create a teacher first "
                    "(e.g. `python manage.py create_teacher ...`)."
                )

        tenant, _ = Tenant.objects.get_or_create(
            teacher_profile=teacher,
            defaults={
                "name": teacher.user.get_full_name()
                or teacher.user.username
                or f"Teacher {teacher.id}",
            },
        )
        return teacher, tenant

    def _ensure_demo_student(self, spec: dict, tenant: Tenant):
        user, created = User.objects.get_or_create(
            username=spec["username"],
            defaults={
                "email": spec["email"],
                "first_name": spec["first_name"],
                "last_name": spec["last_name"],
                "role": User.Role.STUDENT,
                "is_active": True,
                "is_email_verified": True,
            },
        )
        if created:
            user.set_password(DEMO_STUDENT_PASSWORD)
            user.save(update_fields=["password"])
        else:
            updated_fields = []
            if user.role != User.Role.STUDENT:
                user.role = User.Role.STUDENT
                updated_fields.append("role")
            if not user.is_active:
                user.is_active = True
                updated_fields.append("is_active")
            if not user.is_email_verified:
                user.is_email_verified = True
                updated_fields.append("is_email_verified")
            if updated_fields:
                user.save(update_fields=updated_fields)

        StudentProfile.objects.get_or_create(user=user)
        wallet = get_or_create_wallet(
            tenant=tenant,
            user=user,
            currency=tenant.default_currency,
        )

        idem_key = f"seed-finance-demo:tenant:{tenant.id}:credit:{user.username}"
        if not LedgerEntry.objects.filter(idempotency_key=idem_key).exists():
            credit_wallet(
                tenant=tenant,
                wallet=wallet,
                amount=spec["seed_amount"],
                idempotency_key=idem_key,
                reference_id=f"demo_seed_{user.username}",
                description=f"QA demo deposit for {user.get_full_name() or user.username}",
            )
            wallet.refresh_from_db()

        return user, wallet

    def _ensure_available_balance(
        self, tenant: Tenant, student, needed: Decimal, tag: str
    ):
        wallet = get_or_create_wallet(
            tenant=tenant,
            user=student,
            currency=tenant.default_currency,
        )
        if wallet.available_balance < needed:
            topup = needed - wallet.available_balance
            idem_key = (
                f"seed-finance-demo:tenant:{tenant.id}:topup:{student.username}:{tag}"
            )
            if not LedgerEntry.objects.filter(idempotency_key=idem_key).exists():
                credit_wallet(
                    tenant=tenant,
                    wallet=wallet,
                    amount=topup,
                    idempotency_key=idem_key,
                    reference_id=f"demo_topup_{tag}_{student.username}",
                    description=f"QA demo top-up ({tag})",
                )

    def _ensure_payout_scenarios(
        self, tenant: Tenant, teacher_user, students_by_username
    ):
        alice = students_by_username["demo_student_alice"]
        bob = students_by_username["demo_student_bob"]
        clara = students_by_username["demo_student_clara"]

        results = {}

        # Scenario A: REQUESTED (ready for Mark Processing & Reject Payout)
        scenario_a = Payout.objects.filter(
            tenant=tenant,
            student=alice,
            destination=SCENARIO_DESTINATIONS["requested"],
            status=Payout.Status.REQUESTED,
        ).first()
        if scenario_a is None:
            amount_a = Decimal("30.00")
            self._ensure_available_balance(tenant, alice, amount_a, "scenario_a")
            scenario_a = create_payout_request(
                tenant=tenant,
                student=alice,
                amount_gross=amount_a,
                destination=SCENARIO_DESTINATIONS["requested"],
            )
            verify_and_reserve_payout(
                tenant=tenant,
                payout=scenario_a,
                raw_token=scenario_a.raw_token,
            )
            scenario_a.refresh_from_db()
        results["A (REQUESTED)"] = scenario_a

        # Scenario B: PROCESSING (ready for Complete Payout)
        scenario_b = Payout.objects.filter(
            tenant=tenant,
            student=bob,
            destination=SCENARIO_DESTINATIONS["processing"],
            status=Payout.Status.PROCESSING,
        ).first()
        if scenario_b is None:
            amount_b = Decimal("45.00")
            self._ensure_available_balance(tenant, bob, amount_b, "scenario_b")
            scenario_b = create_payout_request(
                tenant=tenant,
                student=bob,
                amount_gross=amount_b,
                destination=SCENARIO_DESTINATIONS["processing"],
            )
            verify_and_reserve_payout(
                tenant=tenant,
                payout=scenario_b,
                raw_token=scenario_b.raw_token,
            )
            mark_payout_processing(
                tenant=tenant,
                payout=scenario_b,
                admin_user=teacher_user,
            )
            scenario_b.refresh_from_db()
        results["B (PROCESSING)"] = scenario_b

        # Scenario C: COMPLETED with known external_ref for duplicate-txhash testing
        scenario_c = Payout.objects.filter(
            provider="crypto_usdt",
            external_ref=DEMO_COMPLETED_TXHASH,
        ).first()
        if scenario_c is None:
            amount_c = Decimal("25.00")
            self._ensure_available_balance(tenant, alice, amount_c, "scenario_c")
            scenario_c = create_payout_request(
                tenant=tenant,
                student=alice,
                amount_gross=amount_c,
                destination=SCENARIO_DESTINATIONS["completed"],
            )
            verify_and_reserve_payout(
                tenant=tenant,
                payout=scenario_c,
                raw_token=scenario_c.raw_token,
            )
            mark_payout_processing(
                tenant=tenant,
                payout=scenario_c,
                admin_user=teacher_user,
            )
            settle_payout(
                tenant=tenant,
                payout=scenario_c,
                external_ref=DEMO_COMPLETED_TXHASH,
                admin_user=teacher_user,
            )
            scenario_c.refresh_from_db()
        results["C (COMPLETED)"] = scenario_c

        # Scenario D: PENDING_CONFIRMATION (must stay hidden from active review queue)
        scenario_d = Payout.objects.filter(
            tenant=tenant,
            student=clara,
            destination=SCENARIO_DESTINATIONS["pending_confirmation"],
            status=Payout.Status.PENDING_CONFIRMATION,
        ).first()
        if scenario_d is None:
            amount_d = Decimal("20.00")
            self._ensure_available_balance(tenant, clara, amount_d, "scenario_d")
            scenario_d = create_payout_request(
                tenant=tenant,
                student=clara,
                amount_gross=amount_d,
                destination=SCENARIO_DESTINATIONS["pending_confirmation"],
            )
            scenario_d.refresh_from_db()
        results["D (PENDING_CONFIRMATION)"] = scenario_d

        return results

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to run seed_finance_demo when settings.DEBUG is False. "
                "Pass --force if you intentionally want to seed in this environment."
            )

        teacher, tenant = self._resolve_teacher_and_tenant(options["teacher"])
        teacher_user = teacher.user

        # Suppress real SMTP delivery while still exercising full payout services & on_commit hooks.
        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
        ):
            students_by_username = {}
            wallets_by_username = {}
            for spec in DEMO_STUDENTS:
                user, wallet = self._ensure_demo_student(spec, tenant)
                students_by_username[user.username] = user
                wallets_by_username[user.username] = wallet

            scenarios = self._ensure_payout_scenarios(
                tenant, teacher_user, students_by_username
            )

        for wallet in wallets_by_username.values():
            wallet.refresh_from_db()

        login_url = reverse("accounts:login")
        finance_url = reverse("payments:teacher_finance")

        self.stdout.write(self.style.SUCCESS("Finance QA demo data seeded."))
        self.stdout.write(f"Teacher username : {teacher_user.username}")
        self.stdout.write(f"Teacher email    : {teacher_user.email or '(none)'}")
        self.stdout.write(
            f"Tenant           : {tenant.name} (id={tenant.id}, {tenant.default_currency})"
        )
        self.stdout.write(f"Login URL        : {login_url}")
        self.stdout.write(f"Finance Tab URL  : {finance_url}")
        self.stdout.write("")
        self.stdout.write("Demo Students & Wallets:")
        for username, student in students_by_username.items():
            wallet = wallets_by_username[username]
            self.stdout.write(
                f"  - {student.get_full_name()} ({student.username} / {student.email}) "
                f"| settled=${wallet.settled_balance:.2f}, reserved=${wallet.reserved_balance:.2f}, "
                f"available=${wallet.available_balance:.2f}"
            )

        self.stdout.write("")
        self.stdout.write("Seeded Payout Scenarios:")
        for label, payout in scenarios.items():
            ref_info = (
                f", external_ref={payout.external_ref}" if payout.external_ref else ""
            )
            self.stdout.write(
                f"  - Scenario {label}: payout #{payout.id} | student={payout.student.username} "
                f"| gross=${payout.amount_gross:.2f} | dest={payout.destination}{ref_info}"
            )
        self.stdout.write("")
        self.stdout.write(
            f"Duplicate txhash for Complete modal test: {DEMO_COMPLETED_TXHASH}"
        )
