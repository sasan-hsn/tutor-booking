from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from accounts.tests.base import RoleTestCase
from booking.models import Booking, Review


class StudentDashboardPaginationTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tz = ZoneInfo(cls.student_user.timezone)

    def _create_booking(self, student, start_at, end_at, status=Booking.Status.CONFIRMED, lesson_type=Booking.LessonType.REGULAR):
        return Booking.objects.create(
            student=student,
            teacher=self.teacher,
            start_at=start_at,
            end_at=end_at,
            status=status,
            lesson_type=lesson_type,
        )

    def test_student_dashboard_empty_states(self):
        """When student has 0 bookings, empty states are rendered with no sentinels or arrows."""
        response = self.student_client.get(reverse('booking:student_dashboard'))
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "You don't have any upcoming lessons.")
        self.assertContains(response, "You don't have any past lessons yet.")
        self.assertNotContains(response, 'class="carousel-sentinel"')
        self.assertNotContains(response, 'class="scroll-arrow')

    def test_student_dashboard_ssr_initial_batching_upcoming(self):
        """SSR renders only the first 6 upcoming lessons chronologically, with a sentinel if has_next."""
        base_time = timezone.now() + timedelta(days=1)
        created_bookings = []
        for i in range(8):
            start = base_time + timedelta(hours=i * 2)
            end = start + timedelta(hours=1)
            b = self._create_booking(self.student_user, start, end)
            created_bookings.append(b)

        response = self.student_client.get(reverse('booking:student_dashboard'))
        self.assertEqual(response.status_code, 200)

        # Context has page with 6 items and has_next=True
        upcoming_page = response.context['upcoming_bookings']
        self.assertEqual(len(upcoming_page), 6)
        self.assertTrue(response.context['upcoming_has_next'])

        # First 6 rendered in HTML
        for b in created_bookings[:6]:
            self.assertContains(response, f'data-booking-id="{b.id}"')
        # 7th and 8th are not in initial SSR
        for b in created_bookings[6:]:
            self.assertNotContains(response, f'data-booking-id="{b.id}"')

        # Sentinel is rendered with data-section="upcoming"
        self.assertContains(response, 'class="carousel-sentinel" data-section="upcoming"')

    def test_student_dashboard_ssr_initial_batching_past(self):
        """SSR renders only the first 6 past lessons reverse-chronologically, with a sentinel if has_next."""
        now = timezone.now()
        created_bookings = []
        for i in range(8):
            start = now - timedelta(days=i + 1)
            end = start + timedelta(hours=1)
            b = self._create_booking(self.student_user, start, end, status=Booking.Status.COMPLETED)
            created_bookings.append(b)

        response = self.student_client.get(reverse('booking:student_dashboard'))
        self.assertEqual(response.status_code, 200)

        # Context has page with 6 items and has_next=True
        past_page = response.context['past_bookings']
        self.assertEqual(len(past_page), 6)
        self.assertTrue(response.context['past_has_next'])

        # First 6 rendered in HTML
        for b in created_bookings[:6]:
            self.assertContains(response, f'data-booking-id="{b.id}"')
        # 7th and 8th are not in initial SSR
        for b in created_bookings[6:]:
            self.assertNotContains(response, f'data-booking-id="{b.id}"')

        # Sentinel is rendered with data-section="past"
        self.assertContains(response, 'class="carousel-sentinel" data-section="past"')

    def test_student_dashboard_ssr_past_lessons_includes_concluded_awaiting_resolution(self):
        """Past lessons include COMPLETED bookings and confirmed bookings where end_at < now (awaiting resolution)."""
        now = timezone.now()

        # 1 completed booking
        b_completed = self._create_booking(
            self.student_user,
            now - timedelta(days=2),
            now - timedelta(days=2) + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )

        # 1 confirmed booking whose end_at is in the past (awaiting teacher resolution)
        b_awaiting = self._create_booking(
            self.student_user,
            now - timedelta(hours=3),
            now - timedelta(hours=2),
            status=Booking.Status.CONFIRMED,
        )

        response = self.student_client.get(reverse('booking:student_dashboard'))
        self.assertEqual(response.status_code, 200)

        past_bookings = response.context['past_bookings']
        past_ids = [b.id for b in past_bookings]
        self.assertIn(b_completed.id, past_ids)
        self.assertIn(b_awaiting.id, past_ids)

        # Awaiting resolution badge must be rendered
        self.assertContains(response, 'badge-awaiting-resolution')
        self.assertContains(response, 'Awaiting Resolution')

        # Completed booking has Leave Review
        self.assertContains(response, 'badge-leave-review')

    def test_student_dashboard_excludes_cancelled_and_expired(self):
        """Cancelled and expired bookings must be excluded from both upcoming and past feeds."""
        now = timezone.now()
        # Future cancelled
        self._create_booking(
            self.student_user,
            now + timedelta(days=1),
            now + timedelta(days=1, hours=1),
            status=Booking.Status.CANCELLED,
        )
        # Past cancelled
        self._create_booking(
            self.student_user,
            now - timedelta(days=1),
            now - timedelta(days=1) + timedelta(hours=1),
            status=Booking.Status.CANCELLED,
        )
        # Future expired
        self._create_booking(
            self.student_user,
            now + timedelta(days=2),
            now + timedelta(days=2, hours=1),
            status=Booking.Status.EXPIRED,
        )

        response = self.student_client.get(reverse('booking:student_dashboard'))
        self.assertEqual(len(response.context['upcoming_bookings']), 0)
        self.assertEqual(len(response.context['past_bookings']), 0)

    def test_student_dashboard_past_lessons_ordering(self):
        """Past lessons are ordered reverse-chronologically by start time."""
        now = timezone.now()
        b1 = self._create_booking(
            self.student_user,
            now - timedelta(days=3),
            now - timedelta(days=3) + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )
        b2 = self._create_booking(
            self.student_user,
            now - timedelta(days=1),
            now - timedelta(days=1) + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )
        b3 = self._create_booking(
            self.student_user,
            now - timedelta(days=2),
            now - timedelta(days=2) + timedelta(hours=1),
            status=Booking.Status.CONFIRMED,  # awaiting resolution
        )

        response = self.student_client.get(reverse('booking:student_dashboard'))
        past_bookings = list(response.context['past_bookings'])
        # Reverse chronological by start_at: b2 (-1d), b3 (-2d), b1 (-3d)
        self.assertEqual([b.id for b in past_bookings], [b2.id, b3.id, b1.id])

    def test_student_dashboard_ajax_upcoming_page_2(self):
        """AJAX request for upcoming page 2 returns only card items fragment with headers."""
        base_time = timezone.now() + timedelta(days=1)
        created_bookings = []
        for i in range(8):
            start = base_time + timedelta(hours=i * 2)
            end = start + timedelta(hours=1)
            b = self._create_booking(self.student_user, start, end)
            created_bookings.append(b)

        response = self.student_client.get(
            reverse('booking:student_dashboard') + '?section=upcoming&page=2',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Has-Next'], 'false')
        self.assertEqual(response['X-Next-Page'], '')

        # Should only contain items 7 and 8
        for b in created_bookings[6:]:
            self.assertContains(response, f'data-booking-id="{b.id}"')
        for b in created_bookings[:6]:
            self.assertNotContains(response, f'data-booking-id="{b.id}"')

        # Should be fragment only, no html or body tags
        self.assertNotContains(response, '<html')
        self.assertNotContains(response, 'Upcoming Lessons</h2>')

    def test_student_dashboard_ajax_past_pagination(self):
        """AJAX request for past lessons supports pagination with headers."""
        now = timezone.now()
        created_bookings = []
        for i in range(9):
            start = now - timedelta(days=i + 1)
            end = start + timedelta(hours=1)
            b = self._create_booking(self.student_user, start, end, status=Booking.Status.COMPLETED)
            created_bookings.append(b)

        # Page 1
        response = self.student_client.get(
            reverse('booking:student_dashboard') + '?section=past&page=1',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Has-Next'], 'true')
        self.assertEqual(response['X-Next-Page'], '2')

        # Page 2
        response_page2 = self.student_client.get(
            reverse('booking:student_dashboard') + '?section=past&page=2',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response_page2.status_code, 200)
        self.assertEqual(response_page2['X-Has-Next'], 'false')
        self.assertEqual(response_page2['X-Next-Page'], '')

    def test_student_dashboard_ajax_out_of_bounds_clamping(self):
        """Out of bounds page parameters are clamped gracefully."""
        base_time = timezone.now() + timedelta(days=1)
        for i in range(8):
            start = base_time + timedelta(hours=i * 2)
            end = start + timedelta(hours=1)
            self._create_booking(self.student_user, start, end)

        # page=999 clamps to last page (page 2)
        response = self.student_client.get(
            reverse('booking:student_dashboard') + '?section=upcoming&page=999',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Has-Next'], 'false')

        # page=invalid clamps to page 1
        response = self.student_client.get(
            reverse('booking:student_dashboard') + '?section=upcoming&page=invalid',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Has-Next'], 'true')
        self.assertEqual(response['X-Next-Page'], '2')

    def test_student_dashboard_ajax_invalid_section(self):
        """AJAX request with invalid or missing section returns 400 Bad Request."""
        response = self.student_client.get(
            reverse('booking:student_dashboard') + '?section=unknown',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 400)

        response_no_sec = self.student_client.get(
            reverse('booking:student_dashboard'),
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response_no_sec.status_code, 400)

    def test_student_dashboard_isolation(self):
        """Another student's bookings are never included in the student's dashboard."""
        other_student = User.objects.create_user(
            username='otherstudent',
            email='otherstudent@example.com',
            password='password123',
            role=User.Role.STUDENT,
        )
        now = timezone.now()
        b_other_upcoming = self._create_booking(
            other_student,
            now + timedelta(days=1),
            now + timedelta(days=1, hours=1),
        )
        b_other_past = self._create_booking(
            other_student,
            now - timedelta(days=1),
            now - timedelta(days=1) + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )

        response = self.student_client.get(reverse('booking:student_dashboard'))
        self.assertEqual(len(response.context['upcoming_bookings']), 0)
        self.assertEqual(len(response.context['past_bookings']), 0)
        self.assertNotContains(response, f'data-booking-id="{b_other_upcoming.id}"')
        self.assertNotContains(response, f'data-booking-id="{b_other_past.id}"')
