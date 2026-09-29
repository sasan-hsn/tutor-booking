import io
from datetime import timedelta
from unittest.mock import MagicMock, patch
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import StudentProfile, User
from booking.models import Booking, RegularAvailability, Review, WeeklyOverride
from portfolio.models import TeacherProfile


class VerifyDatabaseIntegrityTests(TestCase):
    def setUp(self):
        super().setUp()
        self.teacher_user = User.objects.create_user(
            username='verified_teacher',
            email='teacher@example.com',
            password='strong_password_123',
            role=User.Role.TEACHER,
            is_email_verified=True,
        )
        self.teacher_profile = self.teacher_user.teacher_profile

        self.student_user = User.objects.create_user(
            username='verified_student',
            email='student@example.com',
            password='strong_password_123',
            role=User.Role.STUDENT,
            is_email_verified=True,
        )
        self.student_profile = self.student_user.student_profile

        start_time = timezone.now() + timedelta(days=2)
        self.booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher_profile,
            start_at=start_time,
            end_at=start_time + timedelta(hours=1),
            status=Booking.Status.CONFIRMED,
        )

        self.regular_avail = RegularAvailability.objects.create(
            teacher=self.teacher_profile,
            day_of_week=0,
            start_time='09:00',
            end_time='17:00',
        )

        self.weekly_override = WeeklyOverride.objects.create(
            teacher=self.teacher_profile,
            date=timezone.now().date() + timedelta(days=5),
            start_time='10:00',
            end_time='12:00',
            is_available=True,
        )

    def test_clean_database_passes_all_checks(self):
        out = io.StringIO()
        call_command('verify_database_integrity', stdout=out)
        output = out.getvalue()

        self.assertIn('Database Integrity Verification', output)
        self.assertIn('[OK] All migrations applied', output)
        self.assertIn('Users:', output)
        self.assertIn('Bookings:', output)
        self.assertIn('[OK] All foreign keys and profiles intact', output)
        self.assertIn('password hashes inspected; all valid', output)
        self.assertIn('PASSED', output)

    def test_database_flag_accepted(self):
        out = io.StringIO()
        call_command('verify_database_integrity', database='default', stdout=out)
        self.assertIn('Database: default', out.getvalue())
        self.assertIn('PASSED', out.getvalue())

    def test_nonexistent_database_flag_raises_command_error(self):
        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command('verify_database_integrity', database='invalid_db_alias', stdout=out)
        self.assertIn("does not exist in settings.DATABASES", str(ctx.exception))

    def test_check_media_disabled_skips_media_check(self):
        self.teacher_profile.profile_picture = 'profile_pictures/nonexistent_pic.jpg'
        self.teacher_profile.save()

        out = io.StringIO()
        call_command('verify_database_integrity', check_media=False, stdout=out)
        output = out.getvalue()

        self.assertIn('Media check skipped', output)
        self.assertNotIn('MISSING MEDIA', output)
        self.assertIn('PASSED', output)

    def test_missing_media_produces_warning_by_default(self):
        self.teacher_profile.profile_picture = 'profile_pictures/ghost_avatar.jpg'
        self.teacher_profile.save()

        out = io.StringIO()
        call_command('verify_database_integrity', stdout=out)
        output = out.getvalue()

        self.assertIn('MISSING MEDIA', output)
        self.assertIn('ghost_avatar.jpg', output)
        # By default, missing media produces a warning, but command still succeeds
        self.assertIn('PASSED', output)

    def test_missing_media_fails_when_fail_on_missing_media_is_true(self):
        self.teacher_profile.profile_picture = 'profile_pictures/ghost_avatar.jpg'
        self.teacher_profile.save()

        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command('verify_database_integrity', fail_on_missing_media=True, stdout=out)

        self.assertIn('Database integrity verification failed', str(ctx.exception))
        self.assertIn('MISSING MEDIA', out.getvalue())

    def test_present_media_file_passes_check(self):
        file_name = default_storage.save(
            'profile_pictures/valid_test_avatar.jpg',
            ContentFile(b'fake image data')
        )
        self.addCleanup(default_storage.delete, file_name)

        self.teacher_profile.profile_picture = file_name
        self.teacher_profile.save()

        out = io.StringIO()
        call_command('verify_database_integrity', stdout=out)
        output = out.getvalue()

        self.assertIn('referenced media file(s) verified in storage', output)
        self.assertNotIn('MISSING MEDIA', output)
        self.assertIn('PASSED', output)

    def test_sample_auth_disabled_skips_hasher_check(self):
        # Corrupt the password
        User.objects.filter(id=self.student_user.id).update(password='corrupted_raw_password')

        out = io.StringIO()
        call_command('verify_database_integrity', sample_auth=False, stdout=out)
        output = out.getvalue()

        self.assertIn('Password hasher check skipped', output)
        self.assertIn('PASSED', output)

    def test_invalid_password_hash_fails_verification(self):
        User.objects.filter(id=self.student_user.id).update(password='corrupted_raw_password')

        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command('verify_database_integrity', stdout=out)

        self.assertIn('Database integrity verification failed', str(ctx.exception))
        output = out.getvalue()
        self.assertIn('Invalid or unrecognized password hash', output)
        self.assertIn('verified_student', output)

    def test_unsupported_password_hasher_algorithm_fails_verification(self):
        mock_hasher = MagicMock()
        mock_hasher.algorithm = 'unsupported_test_algo'

        out = io.StringIO()
        with patch('accounts.management.commands.verify_database_integrity.identify_hasher', return_value=mock_hasher):
            with self.assertRaises(CommandError) as ctx:
                call_command('verify_database_integrity', stdout=out)

        self.assertIn('Database integrity verification failed', str(ctx.exception))
        output = out.getvalue()
        self.assertIn("unsupported hasher algorithm 'unsupported_test_algo'", output)

    def test_unapplied_migration_detected_fails_verification(self):
        mock_migration = MagicMock()
        mock_migration.app_label = 'accounts'
        mock_migration.name = '9999_fake_migration'

        out = io.StringIO()
        with patch('django.db.migrations.executor.MigrationExecutor.migration_plan', return_value=[(mock_migration, False)]):
            with self.assertRaises(CommandError) as ctx:
                call_command('verify_database_integrity', stdout=out)

        self.assertIn('Database integrity verification failed', str(ctx.exception))
        output = out.getvalue()
        self.assertIn('Unapplied migration: accounts.9999_fake_migration', output)

    def test_referential_integrity_missing_profile_for_teacher(self):
        # Delete the TeacherProfile while keeping the user
        TeacherProfile.objects.filter(id=self.teacher_profile.id).delete()

        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command('verify_database_integrity', stdout=out)

        self.assertIn('Database integrity verification failed', str(ctx.exception))
        output = out.getvalue()
        self.assertIn('Teacher user(s) missing TeacherProfile', output)

    def test_referential_integrity_missing_profile_for_student(self):
        StudentProfile.objects.filter(id=self.student_profile.id).delete()

        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command('verify_database_integrity', stdout=out)

        self.assertIn('Database integrity verification failed', str(ctx.exception))
        output = out.getvalue()
        self.assertIn('Student user(s) missing StudentProfile', output)

    def test_referential_integrity_broken_booking_student_fk(self):
        with patch('accounts.management.commands.verify_database_integrity.Booking.objects.using') as mock_using:
            mock_qs = MagicMock()
            mock_using.return_value = mock_qs
            mock_qs.count.return_value = 1
            mock_qs.filter.return_value.count.return_value = 0

            # Simulate orphaned student FK returning 1
            def mock_exclude(**kwargs):
                sub_mock = MagicMock()
                if 'student__in' in kwargs:
                    sub_mock.count.return_value = 1
                else:
                    sub_mock.count.return_value = 0
                return sub_mock

            mock_qs.exclude.side_effect = mock_exclude

            out = io.StringIO()
            with self.assertRaises(CommandError) as ctx:
                call_command('verify_database_integrity', stdout=out)

            self.assertIn('Database integrity verification failed', str(ctx.exception))
            output = out.getvalue()
            self.assertIn('Booking(s) reference non-existent Student User IDs', output)

    def test_referential_integrity_broken_booking_teacher_fk(self):
        with patch('accounts.management.commands.verify_database_integrity.Booking.objects.using') as mock_using:
            mock_qs = MagicMock()
            mock_using.return_value = mock_qs
            mock_qs.count.return_value = 1
            mock_qs.filter.return_value.count.return_value = 0

            # Simulate orphaned teacher FK returning 1
            def mock_exclude(**kwargs):
                sub_mock = MagicMock()
                if 'teacher__in' in kwargs:
                    sub_mock.count.return_value = 1
                else:
                    sub_mock.count.return_value = 0
                return sub_mock

            mock_qs.exclude.side_effect = mock_exclude

            out = io.StringIO()
            with self.assertRaises(CommandError) as ctx:
                call_command('verify_database_integrity', stdout=out)

            self.assertIn('Database integrity verification failed', str(ctx.exception))
            output = out.getvalue()
            self.assertIn('Booking(s) reference non-existent TeacherProfile IDs', output)

            self.assertIn('Database integrity verification failed', str(ctx.exception))
            output = out.getvalue()
            self.assertIn('Booking(s) reference non-existent TeacherProfile IDs', output)

    def test_referential_integrity_mismatched_review_student(self):
        # Create completed booking and review
        past_time = timezone.now() - timedelta(days=2)
        completed_booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher_profile,
            start_at=past_time,
            end_at=past_time + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )
        review = Review.objects.create(
            student=self.student_user,
            booking=completed_booking,
            rating=5,
            comment='Great lesson!',
        )
        other_student = User.objects.create_user(
            username='other_student',
            password='password123',
            role=User.Role.STUDENT,
        )
        # Force review.student_id to other_student
        Review.objects.filter(id=review.id).update(student_id=other_student.id)

        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command('verify_database_integrity', stdout=out)

        self.assertIn('Database integrity verification failed', str(ctx.exception))
        output = out.getvalue()
        self.assertIn('Review(s) where student does not match booking student', output)

    def test_formatted_entity_counts_output(self):
        out = io.StringIO()
        call_command('verify_database_integrity', stdout=out)
        output = out.getvalue()

        self.assertIn('Users: 2 (Teachers: 1, Students: 1', output)
        self.assertIn('Profiles: 1 TeacherProfile(s), 1 StudentProfile(s)', output)
        self.assertIn('Bookings: 1', output)
        self.assertIn('Confirmed: 1', output)
        self.assertIn('Availabilities: 1 RegularAvailability rule(s), 1 WeeklyOverride(s)', output)
