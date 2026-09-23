from django.template.loader import render_to_string
from django.test import SimpleTestCase


class EmailTemplateTests(SimpleTestCase):
    def test_global_base_email_renders_brand_elements(self):
        context = {
            'site_name': 'English with Mary',
            'site_url': 'https://englishwithmary.ir',
            'site_domain': 'englishwithmary.ir',
            'header_title': 'English with Mary',
            'header_subtitle': 'Welcome',
        }
        rendered = render_to_string('emails/base_email.html', context)
        self.assertIn('English with Mary', rendered)
        self.assertIn('https://englishwithmary.ir', rendered)
        self.assertIn('#4F7A62', rendered)
        self.assertIn('This is an automated notification from English with Mary.', rendered)

    def test_booking_base_email_inherits_and_customizes_footer(self):
        context = {
            'site_name': 'English with Mary',
            'site_url': 'https://englishwithmary.ir',
            'site_domain': 'englishwithmary.ir',
            'header_title': 'Lesson Confirmed',
        }
        rendered = render_to_string('booking/emails/base_email.html', context)
        self.assertIn('Lesson Confirmed', rendered)
        self.assertIn('This is an automated notification regarding your lesson booking.', rendered)

    def test_verify_email_templates_render(self):
        context = {
            'user_name': 'Jane Doe',
            'verification_url': 'https://englishwithmary.ir/accounts/verify-email/dummy-token/',
            'site_name': 'English with Mary',
            'site_url': 'https://englishwithmary.ir',
            'site_domain': 'englishwithmary.ir',
        }
        html_rendered = render_to_string('accounts/emails/verify_email.html', context)
        txt_rendered = render_to_string('accounts/emails/verify_email.txt', context)

        self.assertIn('Jane Doe', html_rendered)
        self.assertIn('Verify Email Address', html_rendered)
        self.assertIn('https://englishwithmary.ir/accounts/verify-email/dummy-token/', html_rendered)
        self.assertIn('24 hours', html_rendered)
        self.assertIn('This is an automated email verification link from English with Mary.', html_rendered)

        self.assertIn('Hello Jane Doe', txt_rendered)
        self.assertIn('https://englishwithmary.ir/accounts/verify-email/dummy-token/', txt_rendered)
        self.assertIn('24 hours', txt_rendered)
