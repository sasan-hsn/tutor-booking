import argparse
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import get_hashers, identify_hasher, is_password_usable
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, models
from django.db.migrations.executor import MigrationExecutor
from django.utils.connection import ConnectionDoesNotExist

from accounts.models import StudentProfile
from booking.models import Booking, RegularAvailability, Review, WeeklyOverride
from portfolio.models import Certificate, TeacherProfile


class Command(BaseCommand):
    help = (
        'Verifies database integrity after a restore or disaster recovery drill. '
        'Checks schema migrations, entity counts, referential integrity, '
        'password hasher sanity, and on-disk media existence.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--database',
            default='default',
            help='Target database alias to verify (default: "default").',
        )
        parser.add_argument(
            '--check-media',
            action=argparse.BooleanOptionalAction,
            default=True,
            help='Check existence of referenced media files in storage (default: True).',
        )
        parser.add_argument(
            '--sample-auth',
            action=argparse.BooleanOptionalAction,
            default=True,
            help='Verify password hasher validity using Django hasher (default: True).',
        )
        parser.add_argument(
            '--fail-on-missing-media',
            action='store_true',
            default=False,
            help='Treat missing media files as a fatal error instead of a warning.',
        )

    def handle(self, *args, **options):
        db = options['database']
        check_media = options.get('check_media', True)
        sample_auth = options.get('sample_auth', True)
        fail_on_missing_media = options.get('fail_on_missing_media', False)

        errors = []
        warnings = []

        self.stdout.write(self.style.MIGRATE_HEADING('=' * 50))
        self.stdout.write(self.style.MIGRATE_HEADING('Database Integrity Verification'))
        self.stdout.write(f'Database: {db}')
        self.stdout.write(self.style.MIGRATE_HEADING('=' * 50))

        try:
            conn = connections[db]
            conn.ensure_connection()
        except ConnectionDoesNotExist:
            raise CommandError(f"Database connection '{db}' does not exist in settings.DATABASES.")
        except Exception as ex:
            raise CommandError(f"Unable to connect to database '{db}': {ex}")

        # 1. Schema Migrations Check
        self._check_migrations(conn, errors)

        # 2. Formatted Core Entity Counts
        self._check_entity_counts(db)

        # 3. Referential Integrity Check
        self._check_referential_integrity(db, errors)

        # 4. Password Hasher Sanity Check
        self._check_password_hashers(db, sample_auth, errors)

        # 5. Media Storage Cross-Check
        self._check_media_storage(db, check_media, fail_on_missing_media, errors, warnings)

        # Final Summary & Exit
        self._render_summary(errors, warnings)

    def _check_migrations(self, conn, errors):
        self.stdout.write('\n[1/5] Schema Migrations')
        executor = MigrationExecutor(conn)
        targets = executor.loader.graph.leaf_nodes()
        unapplied_plan = executor.migration_plan(targets)

        if unapplied_plan:
            for migration, _ in unapplied_plan:
                err = f'Unapplied migration: {migration.app_label}.{migration.name}'
                errors.append(err)
                self.stdout.write(self.style.ERROR(f'  [FAIL] {err}'))
        else:
            self.stdout.write(self.style.SUCCESS('  [OK] All migrations applied (0 unapplied).'))

    def _check_entity_counts(self, db):
        self.stdout.write('\n[2/5] Core Entity Counts')
        User = get_user_model()
        users_qs = User.objects.using(db)
        total_users = users_qs.count()
        teachers_count = users_qs.filter(role=User.Role.TEACHER).count()
        students_count = users_qs.filter(role=User.Role.STUDENT).count()
        active_users = users_qs.filter(is_active=True).count()
        verified_users = users_qs.filter(is_email_verified=True).count()
        unverified_users = users_qs.filter(is_email_verified=False).count()

        teacher_profiles_count = TeacherProfile.objects.using(db).count()
        student_profiles_count = StudentProfile.objects.using(db).count()

        bookings_qs = Booking.objects.using(db)
        total_bookings = bookings_qs.count()
        status_counts = []
        for status_val, status_label in Booking.Status.choices:
            count = bookings_qs.filter(status=status_val).count()
            status_counts.append(f'{status_label}: {count}')

        reviews_qs = Review.objects.using(db)
        total_reviews = reviews_qs.count()
        approved_reviews = reviews_qs.filter(is_approved=True).count()
        pending_reviews = reviews_qs.filter(is_approved=False).count()

        reg_avail_count = RegularAvailability.objects.using(db).count()
        override_count = WeeklyOverride.objects.using(db).count()
        cert_count = Certificate.objects.using(db).count()

        self.stdout.write(
            f'  Users: {total_users} (Teachers: {teachers_count}, Students: {students_count}, '
            f'Active: {active_users}, Email Verified: {verified_users}, Unverified: {unverified_users})'
        )
        self.stdout.write(
            f'  Profiles: {teacher_profiles_count} TeacherProfile(s), '
            f'{student_profiles_count} StudentProfile(s)'
        )
        self.stdout.write(
            f'  Bookings: {total_bookings} ({", ".join(status_counts)})'
        )
        self.stdout.write(
            f'  Reviews: {total_reviews} (Approved: {approved_reviews}, Pending: {pending_reviews})'
        )
        self.stdout.write(
            f'  Availabilities: {reg_avail_count} RegularAvailability rule(s), '
            f'{override_count} WeeklyOverride(s), {cert_count} Certificate(s)'
        )

    def _check_referential_integrity(self, db, errors):
        self.stdout.write('\n[3/5] Referential Integrity')
        User = get_user_model()
        users_qs = User.objects.using(db)
        teachers_qs = TeacherProfile.objects.using(db)
        bookings_qs = Booking.objects.using(db)
        reviews_qs = Review.objects.using(db)

        relational_errors = []

        # Check profiles referencing non-existent users
        orphaned_teachers = teachers_qs.exclude(user__in=users_qs).count()
        if orphaned_teachers:
            relational_errors.append(f'{orphaned_teachers} TeacherProfile(s) reference non-existent User IDs')

        orphaned_students = StudentProfile.objects.using(db).exclude(user__in=users_qs).count()
        if orphaned_students:
            relational_errors.append(f'{orphaned_students} StudentProfile(s) reference non-existent User IDs')

        # Check users missing required profiles
        missing_tp = users_qs.filter(role=User.Role.TEACHER, teacher_profile__isnull=True).count()
        if missing_tp:
            relational_errors.append(f'{missing_tp} Teacher user(s) missing TeacherProfile')

        missing_sp = users_qs.filter(role=User.Role.STUDENT, student_profile__isnull=True).count()
        if missing_sp:
            relational_errors.append(f'{missing_sp} Student user(s) missing StudentProfile')

        # Check bookings referencing non-existent students or teachers
        orphaned_booking_students = bookings_qs.exclude(student__in=users_qs).count()
        if orphaned_booking_students:
            relational_errors.append(
                f'{orphaned_booking_students} Booking(s) reference non-existent Student User IDs'
            )

        orphaned_booking_teachers = bookings_qs.exclude(teacher__in=teachers_qs).count()
        if orphaned_booking_teachers:
            relational_errors.append(
                f'{orphaned_booking_teachers} Booking(s) reference non-existent TeacherProfile IDs'
            )

        # Check reviews referencing non-existent students, bookings, or teachers
        orphaned_review_students = reviews_qs.exclude(student__in=users_qs).count()
        if orphaned_review_students:
            relational_errors.append(
                f'{orphaned_review_students} Review(s) reference non-existent Student User IDs'
            )

        orphaned_review_bookings = reviews_qs.exclude(booking__in=bookings_qs).count()
        if orphaned_review_bookings:
            relational_errors.append(
                f'{orphaned_review_bookings} Review(s) reference non-existent Booking IDs'
            )

        orphaned_review_teachers = reviews_qs.exclude(booking__teacher__in=teachers_qs).count()
        if orphaned_review_teachers:
            relational_errors.append(
                f'{orphaned_review_teachers} Review(s) reference non-existent TeacherProfile IDs via Booking'
            )

        # Check reviews where review.student != booking.student
        mismatched_reviews = reviews_qs.exclude(student=models.F('booking__student')).count()
        if mismatched_reviews:
            relational_errors.append(
                f'{mismatched_reviews} Review(s) where student does not match booking student'
            )

        # Check availability / override records referencing non-existent teachers
        orphaned_reg_avail = RegularAvailability.objects.using(db).exclude(teacher__in=teachers_qs).count()
        if orphaned_reg_avail:
            relational_errors.append(
                f'{orphaned_reg_avail} RegularAvailability rule(s) reference non-existent TeacherProfile IDs'
            )

        orphaned_overrides = WeeklyOverride.objects.using(db).exclude(teacher__in=teachers_qs).count()
        if orphaned_overrides:
            relational_errors.append(
                f'{orphaned_overrides} WeeklyOverride(s) reference non-existent TeacherProfile IDs'
            )

        orphaned_certs = Certificate.objects.using(db).exclude(teacher__in=teachers_qs).count()
        if orphaned_certs:
            relational_errors.append(
                f'{orphaned_certs} Certificate(s) reference non-existent TeacherProfile IDs'
            )

        if relational_errors:
            for r_err in relational_errors:
                errors.append(f'Referential integrity error: {r_err}')
                self.stdout.write(self.style.ERROR(f'  [FAIL] {r_err}'))
        else:
            self.stdout.write(self.style.SUCCESS('  [OK] All foreign keys and profiles intact.'))

    def _check_password_hashers(self, db, sample_auth, errors):
        self.stdout.write('\n[4/5] Password Hasher Sanity')
        if not sample_auth:
            self.stdout.write(self.style.NOTICE('  [SKIPPED] Password hasher check skipped via flag.'))
            return

        User = get_user_model()
        hasher_errors = []
        inspected_count = 0
        configured_algorithms = {h.algorithm for h in get_hashers()}

        for user in User.objects.using(db).iterator():
            inspected_count += 1
            pwd = user.password
            if not pwd:
                hasher_errors.append(f"User '{user.username}' (id={user.id}) has an empty password field")
                continue

            if is_password_usable(pwd):
                try:
                    hasher = identify_hasher(pwd)
                    if hasher.algorithm not in configured_algorithms:
                        hasher_errors.append(
                            f"User '{user.username}' (id={user.id}) uses unsupported hasher "
                            f"algorithm '{hasher.algorithm}'"
                        )
                except ValueError as ex:
                    hasher_errors.append(
                        f"User '{user.username}' (id={user.id}) has an "
                        f"Invalid or unrecognized password hash: {ex}"
                    )
            else:
                if not pwd.startswith('!'):
                    hasher_errors.append(
                        f"User '{user.username}' (id={user.id}) has malformed unusable password"
                    )

        if hasher_errors:
            for h_err in hasher_errors:
                errors.append(f'Password hasher error: {h_err}')
                self.stdout.write(self.style.ERROR(f'  [FAIL] {h_err}'))
        else:
            self.stdout.write(
                self.style.SUCCESS(f'  [OK] {inspected_count} password hashes inspected; all valid.')
            )

    def _check_media_storage(self, db, check_media, fail_on_missing_media, errors, warnings):
        self.stdout.write('\n[5/5] Media Storage Cross-Check')
        if not check_media:
            self.stdout.write(self.style.NOTICE('  [SKIPPED] Media check skipped via flag.'))
            return

        missing_media = []
        verified_media_count = 0

        def inspect_file(field_file, label):
            nonlocal verified_media_count
            if field_file and field_file.name:
                if default_storage.exists(field_file.name):
                    verified_media_count += 1
                else:
                    missing_media.append(f"{label}: '{field_file.name}'")

        # TeacherProfile images
        for teacher in TeacherProfile.objects.using(db).iterator():
            t_user_id = teacher.user_id
            inspect_file(
                teacher.profile_picture,
                f"TeacherProfile id={teacher.id} (user_id={t_user_id}) profile_picture"
            )
            inspect_file(
                teacher.hero_image,
                f"TeacherProfile id={teacher.id} (user_id={t_user_id}) hero_image"
            )

        # StudentProfile images
        for student in StudentProfile.objects.using(db).iterator():
            s_user_id = student.user_id
            inspect_file(
                student.profile_picture,
                f"StudentProfile id={student.id} (user_id={s_user_id}) profile_picture"
            )

        if missing_media:
            for m_err in missing_media:
                msg = f'[MISSING MEDIA] {m_err}'
                if fail_on_missing_media:
                    errors.append(msg)
                    self.stdout.write(self.style.ERROR(f'  [FAIL] {msg}'))
                else:
                    warnings.append(msg)
                    self.stdout.write(self.style.WARNING(f'  [WARN] {msg}'))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'  [OK] {verified_media_count} referenced media file(s) verified in storage.'
                )
            )

    def _render_summary(self, errors, warnings):
        self.stdout.write(self.style.MIGRATE_HEADING('\n' + '=' * 50))
        if errors:
            self.stdout.write(
                self.style.ERROR(
                    f'Verification Summary: FAILED ({len(errors)} error(s), {len(warnings)} warning(s))'
                )
            )
            for err in errors:
                self.stdout.write(self.style.ERROR(f'  - {err}'))
            self.stdout.write(self.style.MIGRATE_HEADING('=' * 50))
            raise CommandError(f'Database integrity verification failed ({len(errors)} error(s)).')

        summary_msg = f'Verification Summary: PASSED (0 errors, {len(warnings)} warning(s))'
        if warnings:
            self.stdout.write(self.style.WARNING(summary_msg))
            for warn in warnings:
                self.stdout.write(self.style.WARNING(f'  - {warn}'))
        else:
            self.stdout.write(self.style.SUCCESS(summary_msg))
        self.stdout.write(self.style.MIGRATE_HEADING('=' * 50))
