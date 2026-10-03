from decimal import Decimal
from django.urls import reverse
from accounts.tests.base import RoleTestCase
from portfolio.models import TeacherProfile


class LandingPagePricingTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.lesson_price = Decimal('40.00')
        self.teacher.lesson_price_25 = Decimal('22.00')
        self.teacher.offers_trial = True
        self.teacher.trial_price = Decimal('12.00')
        self.teacher.save()

    def test_landing_page_displays_tiered_pricing(self):
        response = self.client.get(reverse('portfolio:landing_page'))
        self.assertEqual(response.status_code, 200)

        # Check 25-minute lesson pricing and duration
        self.assertContains(response, '22.00')
        self.assertContains(response, '25 min')

        # Check 50-minute lesson pricing and duration
        self.assertContains(response, '40.00')
        self.assertContains(response, '50 min')

        # Check trial session rate
        self.assertContains(response, '12.00')

    def test_landing_page_displays_free_trial_when_trial_price_none(self):
        self.teacher.trial_price = None
        self.teacher.save()

        response = self.client.get(reverse('portfolio:landing_page'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'First trial session free')

    def test_landing_page_hides_trial_when_not_offered(self):
        self.teacher.offers_trial = False
        self.teacher.save()

        response = self.client.get(reverse('portfolio:landing_page'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Trial session')
        self.assertNotContains(response, 'First trial session free')

    def test_landing_page_empty_state_when_no_teacher_profile(self):
        TeacherProfile.objects.all().delete()
        response = self.client.get(reverse('portfolio:landing_page'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Teacher Profile Coming Soon')
