from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class BookingDurationMinutesMigrationTests(TransactionTestCase):
    migrate_from = [
        ("booking", "0010_teacherdailydigestrecord"),
        ("accounts", "0006_grandfather_existing_users"),
        ("portfolio", "0011_teacherprofile_lesson_price_25"),
    ]
    migrate_to = [
        ("booking", "0011_booking_duration_minutes"),
        ("accounts", "0006_grandfather_existing_users"),
        ("portfolio", "0011_teacherprofile_lesson_price_25"),
    ]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        # Migrate backward to 0010 state
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        OldUser = old_apps.get_model("accounts", "User")
        OldTeacherProfile = old_apps.get_model("portfolio", "TeacherProfile")
        OldBooking = old_apps.get_model("booking", "Booking")

        # Create teacher and student
        self.teacher_user = OldUser.objects.create(
            username="mig_teacher",
            email="mig_teacher@example.com",
            role="teacher",
            timezone="UTC",
        )
        self.teacher_profile = OldTeacherProfile.objects.create(
            user=self.teacher_user,
        )
        self.student_user = OldUser.objects.create(
            username="mig_student",
            email="mig_student@example.com",
            role="student",
            timezone="UTC",
        )

        base_time = datetime(2026, 10, 10, 10, 0, tzinfo=ZoneInfo("UTC"))

        # 1. Trial booking with 25-minute interval (10:00 - 10:25)
        self.trial_25 = OldBooking.objects.create(
            student=self.student_user,
            teacher=self.teacher_profile,
            start_at=base_time,
            end_at=base_time + timedelta(minutes=25),
            lesson_type="trial",
        )

        # 2. Regular booking with standard 50-minute interval (11:00 - 11:50)
        self.regular_50 = OldBooking.objects.create(
            student=self.student_user,
            teacher=self.teacher_profile,
            start_at=base_time + timedelta(hours=1),
            end_at=base_time + timedelta(hours=1, minutes=50),
            lesson_type="regular",
        )

        # 3. Regular booking with 25-minute interval (12:00 - 12:25)
        self.regular_25 = OldBooking.objects.create(
            student=self.student_user,
            teacher=self.teacher_profile,
            start_at=base_time + timedelta(hours=2),
            end_at=base_time + timedelta(hours=2, minutes=25),
            lesson_type="regular",
        )

        # 4. Regular booking with custom 60-minute interval (13:00 - 14:00)
        self.regular_60 = OldBooking.objects.create(
            student=self.student_user,
            teacher=self.teacher_profile,
            start_at=base_time + timedelta(hours=3),
            end_at=base_time + timedelta(hours=4),
            lesson_type="regular",
        )

        # Run forward migration to 0011
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        self.new_apps = self.executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        # Restore database to latest migrations
        self.executor.loader.build_graph()
        self.executor.migrate(self.executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_migration_backfills_durations_accurately(self):
        NewBooking = self.new_apps.get_model("booking", "Booking")

        b_trial = NewBooking.objects.get(pk=self.trial_25.pk)
        self.assertEqual(b_trial.duration_minutes, 25)

        b_reg50 = NewBooking.objects.get(pk=self.regular_50.pk)
        self.assertEqual(b_reg50.duration_minutes, 50)

        b_reg25 = NewBooking.objects.get(pk=self.regular_25.pk)
        self.assertEqual(b_reg25.duration_minutes, 25)

        b_reg60 = NewBooking.objects.get(pk=self.regular_60.pk)
        self.assertEqual(b_reg60.duration_minutes, 60)

    def test_reverse_migration(self):
        # Migrate backward to 0010
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_from)
        reverted_apps = self.executor.loader.project_state(self.migrate_from).apps
        RevertedBooking = reverted_apps.get_model("booking", "Booking")

        booking = RevertedBooking.objects.get(pk=self.trial_25.pk)
        self.assertFalse(hasattr(booking, "duration_minutes"))
