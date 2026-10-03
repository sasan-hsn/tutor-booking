from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from accounts.tests.base import RoleTestCase
from booking.models import Booking, Review
from portfolio.models import TeacherProfile


class TeacherDashboardPaginationTests(RoleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = cls.teacher_user.teacher_profile
        cls.tz = ZoneInfo(cls.teacher_user.timezone)

    def _create_booking(
        self,
        student,
        start_at,
        end_at,
        teacher=None,
        status=Booking.Status.CONFIRMED,
        lesson_type=Booking.LessonType.REGULAR,
    ):
        return Booking.objects.create(
            student=student,
            teacher=teacher or self.teacher,
            start_at=start_at,
            end_at=end_at,
            status=status,
            lesson_type=lesson_type,
        )

    def test_teacher_dashboard_empty_states(self):
        """When teacher has 0 bookings, empty states are rendered with no sentinels or arrows."""
        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "You don't have any upcoming lessons.")
        self.assertContains(response, "You don't have any past lessons yet.")
        self.assertNotContains(response, 'class="carousel-sentinel"')
        self.assertNotContains(response, 'class="scroll-arrow')

    def test_teacher_dashboard_ssr_initial_batching_upcoming(self):
        """SSR renders only the first 6 upcoming lessons chronologically, with a sentinel if has_next."""
        base_time = timezone.now() + timedelta(days=1)
        created_bookings = []
        for i in range(8):
            start = base_time + timedelta(hours=i * 2)
            end = start + timedelta(hours=1)
            b = self._create_booking(self.student_user, start, end)
            created_bookings.append(b)

        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)

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

        # AJAX url on scroll container points to teacher dashboard
        self.assertContains(response, f'data-ajax-url="{reverse("booking:teacher_dashboard")}"')

    def test_teacher_dashboard_ssr_initial_batching_past(self):
        """SSR renders only the first 6 completed lessons reverse-chronologically, with a sentinel if has_next."""
        now = timezone.now()
        created_bookings = []
        for i in range(8):
            start = now - timedelta(days=i + 1)
            end = start + timedelta(hours=1)
            b = self._create_booking(self.student_user, start, end, status=Booking.Status.COMPLETED)
            created_bookings.append(b)

        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)

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

    def test_teacher_dashboard_upcoming_retains_awaiting_resolution_lessons_at_front(self):
        """Confirmed past lessons awaiting resolution are kept at the front of upcoming lessons with Needs Action badge."""
        now = timezone.now()

        # 2 confirmed past bookings awaiting resolution (end_at in the past)
        b_past1 = self._create_booking(
            self.student_user,
            now - timedelta(days=2),
            now - timedelta(days=2, hours=-1),
            status=Booking.Status.CONFIRMED,
        )
        b_past2 = self._create_booking(
            self.student_user,
            now - timedelta(days=1),
            now - timedelta(days=1, hours=-1),
            status=Booking.Status.CONFIRMED,
        )

        # 5 future confirmed bookings
        future_bookings = []
        for i in range(5):
            start = now + timedelta(days=i + 1)
            end = start + timedelta(hours=1)
            b = self._create_booking(self.student_user, start, end, status=Booking.Status.CONFIRMED)
            future_bookings.append(b)

        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)

        upcoming_page = response.context['upcoming_bookings']
        self.assertEqual(len(upcoming_page), 6)
        self.assertTrue(response.context['upcoming_has_next'])

        # Bookings awaiting resolution must be at the front (ordered by start_at ascending)
        self.assertEqual(upcoming_page[0].id, b_past1.id)
        self.assertEqual(upcoming_page[1].id, b_past2.id)

        # Awaiting resolution cards render Needs Action badge and warning style
        self.assertContains(response, 'lesson-card-needs-action')
        self.assertContains(response, 'Needs Action')

        # Bookings awaiting resolution must NOT appear in past lessons
        past_bookings = response.context['past_bookings']
        past_ids = [b.id for b in past_bookings]
        self.assertNotIn(b_past1.id, past_ids)
        self.assertNotIn(b_past2.id, past_ids)

    def test_teacher_dashboard_past_lessons_strictly_completed(self):
        """Past lessons feed for teacher strictly contains only COMPLETED bookings."""
        now = timezone.now()

        # 1 completed booking
        b_completed = self._create_booking(
            self.student_user,
            now - timedelta(days=3),
            now - timedelta(days=3) + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )

        # 1 confirmed booking whose end_at is past (awaiting resolution, must NOT be in past)
        b_awaiting = self._create_booking(
            self.student_user,
            now - timedelta(days=2),
            now - timedelta(days=2) + timedelta(hours=1),
            status=Booking.Status.CONFIRMED,
        )

        # 1 cancelled booking
        b_cancelled = self._create_booking(
            self.student_user,
            now - timedelta(days=1),
            now - timedelta(days=1) + timedelta(hours=1),
            status=Booking.Status.CANCELLED,
        )

        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)

        past_bookings = response.context['past_bookings']
        self.assertEqual(len(past_bookings), 1)
        self.assertEqual(past_bookings[0].id, b_completed.id)
        past_ids = [b.id for b in past_bookings]
        self.assertNotIn(b_awaiting.id, past_ids)
        self.assertNotIn(b_cancelled.id, past_ids)

        # AJAX past fragment strictly contains only completed bookings
        ajax_response = self.teacher_client.get(
            reverse('booking:teacher_dashboard') + '?section=past&page=1',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertContains(ajax_response, f'data-booking-id="{b_completed.id}"')
        self.assertNotContains(ajax_response, f'data-booking-id="{b_awaiting.id}"')
        self.assertNotContains(ajax_response, f'data-booking-id="{b_cancelled.id}"')

    def test_teacher_dashboard_past_lessons_ordering(self):
        """Teacher past lessons are ordered reverse-chronologically by start time."""
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
            status=Booking.Status.COMPLETED,
        )

        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        past_bookings = list(response.context['past_bookings'])
        self.assertEqual([b.id for b in past_bookings], [b2.id, b3.id, b1.id])

    def test_teacher_dashboard_past_lesson_card_content(self):
        """Teacher past lesson cards display student name, avatar, lesson type, and review rating."""
        now = timezone.now()

        # Lesson with review
        b_reviewed = self._create_booking(
            self.student_user,
            now - timedelta(days=2),
            now - timedelta(days=2) + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
            lesson_type=Booking.LessonType.REGULAR,
        )
        Review.objects.create(
            student=self.student_user,
            booking=b_reviewed,
            rating=5,
            comment="Excellent teacher!",
            is_approved=True,
        )

        # Lesson without review
        b_unreviewed = self._create_booking(
            self.student_user,
            now - timedelta(days=1),
            now - timedelta(days=1) + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
            lesson_type=Booking.LessonType.TRIAL,
        )

        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)

        # Displays student's name
        self.assertContains(response, self.student_user.get_full_name() or self.student_user.username)

        # Displays lesson types
        self.assertContains(response, "Regular Lesson")
        self.assertContains(response, "Trial Lesson")

        # Displays review stars for reviewed lesson
        self.assertContains(response, 'bi-star-fill')

        # Teacher cards must NOT show student "Leave Review" badge
        self.assertNotContains(response, 'badge-leave-review')
        self.assertNotContains(response, 'Leave Review')

        # Lesson detail modal exists with teacher detail URL
        self.assertContains(response, 'id="lessonDetailModal"')
        self.assertContains(response, reverse('booking:lesson_detail', kwargs={'booking_id': 0}))

    def test_teacher_dashboard_ajax_upcoming_page_2(self):
        """AJAX request for upcoming page 2 returns only card items fragment with headers."""
        base_time = timezone.now() + timedelta(days=1)
        created_bookings = []
        for i in range(8):
            start = base_time + timedelta(hours=i * 2)
            end = start + timedelta(hours=1)
            b = self._create_booking(self.student_user, start, end)
            created_bookings.append(b)

        response = self.teacher_client.get(
            reverse('booking:teacher_dashboard') + '?section=upcoming&page=2',
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
        self.assertNotContains(response, 'Teacher Tools')

    def test_teacher_dashboard_ajax_past_pagination(self):
        """AJAX request for past lessons supports pagination with headers."""
        now = timezone.now()
        created_bookings = []
        for i in range(9):
            start = now - timedelta(days=i + 1)
            end = start + timedelta(hours=1)
            b = self._create_booking(self.student_user, start, end, status=Booking.Status.COMPLETED)
            created_bookings.append(b)

        # Page 1
        response = self.teacher_client.get(
            reverse('booking:teacher_dashboard') + '?section=past&page=1',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Has-Next'], 'true')
        self.assertEqual(response['X-Next-Page'], '2')

        # Page 2
        response_page2 = self.teacher_client.get(
            reverse('booking:teacher_dashboard') + '?section=past&page=2',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response_page2.status_code, 200)
        self.assertEqual(response_page2['X-Has-Next'], 'false')
        self.assertEqual(response_page2['X-Next-Page'], '')

    def test_teacher_dashboard_ajax_out_of_bounds_clamping(self):
        """Out of bounds page parameters are clamped gracefully."""
        base_time = timezone.now() + timedelta(days=1)
        for i in range(8):
            start = base_time + timedelta(hours=i * 2)
            end = start + timedelta(hours=1)
            self._create_booking(self.student_user, start, end)

        # page=999 clamps to last page (page 2)
        response = self.teacher_client.get(
            reverse('booking:teacher_dashboard') + '?section=upcoming&page=999',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Has-Next'], 'false')

        # page=invalid clamps to page 1
        response = self.teacher_client.get(
            reverse('booking:teacher_dashboard') + '?section=upcoming&page=invalid',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Has-Next'], 'true')
        self.assertEqual(response['X-Next-Page'], '2')

    def test_teacher_dashboard_ajax_invalid_section(self):
        """AJAX request with invalid or missing section returns 400 Bad Request."""
        response = self.teacher_client.get(
            reverse('booking:teacher_dashboard') + '?section=unknown',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 400)

        response_no_sec = self.teacher_client.get(
            reverse('booking:teacher_dashboard'),
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response_no_sec.status_code, 400)

    def test_teacher_dashboard_tenant_isolation(self):
        """Another teacher's bookings are never included in this teacher's dashboard."""
        other_teacher_user = User.objects.create_user(
            username='otherteacher',
            email='otherteacher@example.com',
            password='password123',
            role=User.Role.TEACHER,
        )
        other_teacher = other_teacher_user.teacher_profile

        now = timezone.now()
        b_other_upcoming = self._create_booking(
            self.student_user,
            now + timedelta(days=1),
            now + timedelta(days=1, hours=1),
            teacher=other_teacher,
        )
        b_other_past = self._create_booking(
            self.student_user,
            now - timedelta(days=1),
            now - timedelta(days=1) + timedelta(hours=1),
            teacher=other_teacher,
            status=Booking.Status.COMPLETED,
        )

        response = self.teacher_client.get(reverse('booking:teacher_dashboard'))
        self.assertEqual(len(response.context['upcoming_bookings']), 0)
        self.assertEqual(len(response.context['past_bookings']), 0)
        self.assertNotContains(response, f'data-booking-id="{b_other_upcoming.id}"')
        self.assertNotContains(response, f'data-booking-id="{b_other_past.id}"')
