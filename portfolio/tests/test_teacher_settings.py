from datetime import date
from decimal import Decimal
from django.urls import reverse
from accounts.tests.base import RoleTestCase
from accounts.models import User
from portfolio.models import Certificate


class TeacherAccountSettingsTests(RoleTestCase):
    def test_get_renders_form_with_current_values(self):
        response = self.teacher_client.get(reverse('portfolio:teacher_settings_account'))
        self.assertEqual(response.status_code, 200)

    def test_valid_post_updates_user(self):
        response = self.teacher_client.post(
            reverse('portfolio:teacher_settings_account'),
            {
                'username': self.teacher_user.username,
                'first_name': 'Mary',
                'last_name': 'Smith',
                'timezone': 'UTC',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.teacher_user.refresh_from_db()
        self.assertEqual(self.teacher_user.first_name, 'Mary')
        self.assertEqual(self.teacher_user.last_name, 'Smith')

    def test_duplicate_username_shows_form_error_not_500(self):
        User.objects.create_user(username='taken_name', password='x', role=User.Role.STUDENT)
        response = self.teacher_client.post(
            reverse('portfolio:teacher_settings_account'),
            {
                'username': 'taken_name',
                'first_name': 'Mary',
                'last_name': 'Smith',
                'timezone': 'UTC',
            },
        )
        self.assertEqual(response.status_code, 200)  # re-renders form, no redirect
        self.assertContains(response, 'already taken')


class TeacherPortfolioSettingsTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile

    def test_valid_post_updates_profile(self):
        response = self.teacher_client.post(
            reverse('portfolio:teacher_settings_portfolio'),
            {
                'headline': 'Learn English fast',
                'bio': 'I love teaching.',
                'teaching_philosophy': 'Practice makes perfect.',
                'intro_video_url': 'https://youtube.com/embed/xyz',
                'contact_email': 'mary@example.com',
                'whatsapp_number': '',
                'telegram_username': '',
                'instagram_username': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.headline, 'Learn English fast')

    def test_certificate_add_returns_json_and_persists(self):
        response = self.teacher_client.post(
            reverse('portfolio:teacher_certificate_add'),
            {
                'title': 'TEFL Certificate',
                'issued_by': 'Cambridge',
                'issue_date': '2022-01-01',
                'certificate_number': '',
                'credential_url': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Certificate.objects.count(), 1)
        cert = Certificate.objects.first()
        self.assertEqual(cert.teacher, self.teacher)

    def test_certificate_delete_removes_it(self):
        cert = Certificate.objects.create(teacher=self.teacher, title='X', issued_by='Y')
        response = self.teacher_client.post(
            reverse('portfolio:teacher_certificate_delete', kwargs={'certificate_id': cert.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Certificate.objects.filter(id=cert.id).exists())

    def test_teacher_cannot_delete_another_teachers_certificate(self):
        other_teacher_user = User.objects.create_user(
            username='other_teacher', password='x', role=User.Role.TEACHER
        )
        other_cert = Certificate.objects.create(
            teacher=other_teacher_user.teacher_profile, title='Not yours', issued_by='Z'
        )
        response = self.teacher_client.post(
            reverse('portfolio:teacher_certificate_delete', kwargs={'certificate_id': other_cert.id})
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Certificate.objects.filter(id=other_cert.id).exists())


class TeacherBookingSettingsTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile

    def test_valid_post_updates_profile(self):
        response = self.teacher_client.post(
            reverse('portfolio:teacher_settings_booking'),
            {
                'lesson_price': '25.00',
                'lesson_duration_minutes': '60',
                'offers_trial': 'on',
                'trial_price': '10.00',
                'trial_duration_minutes': '30',
                'instant_tutoring_enabled': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.lesson_price, Decimal('25.00'))

    def test_negative_price_shows_form_error_not_500(self):
        response = self.teacher_client.post(
            reverse('portfolio:teacher_settings_booking'),
            {
                'lesson_price': '-5.00',
                'lesson_duration_minutes': '60',
                'offers_trial': '',
                'trial_price': '',
                'trial_duration_minutes': '30',
                'instant_tutoring_enabled': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cannot be negative')


class TeacherProfileCompletionTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile

    def test_freshly_signed_up_teacher_is_incomplete(self):
        self.assertFalse(self.teacher.account_complete)
        self.assertFalse(self.teacher.portfolio_complete)
        self.assertFalse(self.teacher.booking_complete)

    def test_account_complete_requires_first_and_last_name(self):
        self.teacher_user.first_name = 'Mary'
        self.teacher_user.last_name = 'Smith'
        self.teacher_user.save()
        self.assertTrue(self.teacher.account_complete)

    def test_booking_complete_requires_positive_price(self):
        self.teacher.lesson_price = Decimal('20.00')
        self.teacher.save()
        self.assertTrue(self.teacher.booking_complete)

    def test_portfolio_complete_requires_all_fields_and_one_contact_method(self):
        self.teacher.headline = 'X'
        self.teacher.bio = 'Y'
        self.teacher.teaching_philosophy = 'Z'
        self.teacher.intro_video_url = 'https://example.com'
        self.teacher.save()
        self.assertFalse(self.teacher.portfolio_complete)

        self.teacher.contact_email = 'mary@example.com'
        self.teacher.save()
        self.assertFalse(self.teacher.portfolio_complete)

        from django.core.files.uploadedfile import SimpleUploadedFile
        self.teacher.hero_image = SimpleUploadedFile(
            'hero.jpg', b'fake-image-content', content_type='image/jpeg'
        )
        self.teacher.save()
        self.assertTrue(self.teacher.portfolio_complete)