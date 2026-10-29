from datetime import timedelta
from django.urls import reverse
from django.utils import timezone

from accounts.tests.base import RoleTestCase
from accounts.models import User
from booking.models import Booking, Review


class LessonLifecycleTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile

    def _make_booking(self, status, hours_from_now, duration_hours=1):
        start = timezone.now() + timedelta(hours=hours_from_now)
        return Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=start,
            end_at=start + timedelta(hours=duration_hours),
            status=status,
        )

    # --- cancel_lesson ---

    def test_cancel_future_confirmed_booking_succeeds(self):
        booking = self._make_booking(Booking.Status.CONFIRMED, hours_from_now=5)
        response = self.teacher_client.post(
            reverse('booking:cancel_lesson', kwargs={'booking_id': booking.id})
        )
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)

    def test_cancel_awaiting_resolution_booking_returns_400(self):
        booking = self._make_booking(Booking.Status.CONFIRMED, hours_from_now=-5)
        response = self.teacher_client.post(
            reverse('booking:cancel_lesson', kwargs={'booking_id': booking.id})
        )
        self.assertEqual(response.status_code, 400)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)  # unchanged

    def test_cancel_another_teachers_booking_returns_404(self):
        other_teacher = User.objects.create_user(
            username='other_t', password='x', role=User.Role.TEACHER
        )
        booking = Booking.objects.create(
            student=self.student_user,
            teacher=other_teacher.teacher_profile,
            start_at=timezone.now() + timedelta(hours=5),
            end_at=timezone.now() + timedelta(hours=6),
            status=Booking.Status.CONFIRMED,
        )
        response = self.teacher_client.post(
            reverse('booking:cancel_lesson', kwargs={'booking_id': booking.id})
        )
        self.assertEqual(response.status_code, 404)

    # --- complete_lesson ---

    def test_complete_awaiting_resolution_booking_succeeds(self):
        booking = self._make_booking(Booking.Status.CONFIRMED, hours_from_now=-5)
        response = self.teacher_client.post(
            reverse('booking:complete_lesson', kwargs={'booking_id': booking.id}),
            {'note': 'Great session'},
        )
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.COMPLETED)
        self.assertEqual(booking.completion_note, 'Great session')

    def test_complete_future_booking_returns_400(self):
        booking = self._make_booking(Booking.Status.CONFIRMED, hours_from_now=5)
        response = self.teacher_client.post(
            reverse('booking:complete_lesson', kwargs={'booking_id': booking.id})
        )
        self.assertEqual(response.status_code, 400)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)  # unchanged

    # --- mark_lesson_not_held ---

    def test_mark_not_held_awaiting_resolution_booking_succeeds(self):
        booking = self._make_booking(Booking.Status.CONFIRMED, hours_from_now=-5)
        response = self.teacher_client.post(
            reverse('booking:mark_lesson_not_held', kwargs={'booking_id': booking.id})
        )
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)

    def test_mark_not_held_future_booking_returns_400(self):
        booking = self._make_booking(Booking.Status.CONFIRMED, hours_from_now=5)
        response = self.teacher_client.post(
            reverse('booking:mark_lesson_not_held', kwargs={'booking_id': booking.id})
        )
        self.assertEqual(response.status_code, 400)

    # --- update_lesson_note ---

    def test_add_lesson_note_to_completed_booking_succeeds(self):
        booking = self._make_booking(Booking.Status.COMPLETED, hours_from_now=-5)
        self.assertFalse(booking.completion_note)
        response = self.teacher_client.post(
            reverse('booking:update_lesson_note', kwargs={'booking_id': booking.id}),
            {'note': 'Read chapter 4 and complete exercises.'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'note': 'Read chapter 4 and complete exercises.'})
        booking.refresh_from_db()
        self.assertEqual(booking.completion_note, 'Read chapter 4 and complete exercises.')

    def test_update_existing_lesson_note_succeeds(self):
        booking = self._make_booking(Booking.Status.COMPLETED, hours_from_now=-5)
        booking.completion_note = 'Read chapter 3.'
        booking.save()
        response = self.teacher_client.post(
            reverse('booking:update_lesson_note', kwargs={'booking_id': booking.id}),
            {'note': 'Read chapter 4 instead.'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'note': 'Read chapter 4 instead.'})
        booking.refresh_from_db()
        self.assertEqual(booking.completion_note, 'Read chapter 4 instead.')

    def test_clear_lesson_note_with_empty_or_whitespace_string(self):
        booking = self._make_booking(Booking.Status.COMPLETED, hours_from_now=-5)
        booking.completion_note = 'Existing note.'
        booking.save()
        response = self.teacher_client.post(
            reverse('booking:update_lesson_note', kwargs={'booking_id': booking.id}),
            {'note': '   \n  \t  '},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'note': ''})
        booking.refresh_from_db()
        self.assertEqual(booking.completion_note, '')

    def test_note_exceeding_2000_chars_returns_400(self):
        booking = self._make_booking(Booking.Status.COMPLETED, hours_from_now=-5)
        booking.completion_note = 'Original note'
        booking.save()
        response = self.teacher_client.post(
            reverse('booking:update_lesson_note', kwargs={'booking_id': booking.id}),
            {'note': 'a' * 2001},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())
        booking.refresh_from_db()
        self.assertEqual(booking.completion_note, 'Original note')

    def test_note_at_2000_chars_succeeds(self):
        booking = self._make_booking(Booking.Status.COMPLETED, hours_from_now=-5)
        response = self.teacher_client.post(
            reverse('booking:update_lesson_note', kwargs={'booking_id': booking.id}),
            {'note': 'a' * 2000},
        )
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(len(booking.completion_note), 2000)

    def test_update_note_on_non_completed_booking_returns_400(self):
        statuses_with_hours = [
            (Booking.Status.CONFIRMED, 5),
            (Booking.Status.PENDING, 10),
            (Booking.Status.CANCELLED, -10),
        ]
        for status, hours in statuses_with_hours:
            booking = self._make_booking(status, hours_from_now=hours)
            response = self.teacher_client.post(
                reverse('booking:update_lesson_note', kwargs={'booking_id': booking.id}),
                {'note': 'Some note'},
            )
            self.assertEqual(response.status_code, 400, f"Expected 400 for status {status}, got {response.status_code}")
            self.assertIn('error', response.json())

    def test_update_note_on_another_teachers_booking_returns_404(self):
        other_teacher = User.objects.create_user(
            username='other_t_note', password='x', role=User.Role.TEACHER
        )
        booking = Booking.objects.create(
            student=self.student_user,
            teacher=other_teacher.teacher_profile,
            start_at=timezone.now() - timedelta(hours=5),
            end_at=timezone.now() - timedelta(hours=4),
            status=Booking.Status.COMPLETED,
        )
        response = self.teacher_client.post(
            reverse('booking:update_lesson_note', kwargs={'booking_id': booking.id}),
            {'note': 'Hacked note'},
        )
        self.assertEqual(response.status_code, 404)

    def test_student_cannot_update_lesson_note(self):
        booking = self._make_booking(Booking.Status.COMPLETED, hours_from_now=-5)
        response = self.student_client.post(
            reverse('booking:update_lesson_note', kwargs={'booking_id': booking.id}),
            {'note': 'Student note'},
        )
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_client_redirected_to_login(self):
        booking = self._make_booking(Booking.Status.COMPLETED, hours_from_now=-5)
        response = self.anon_client.post(
            reverse('booking:update_lesson_note', kwargs={'booking_id': booking.id}),
            {'note': 'Anon note'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_update_lesson_note_requires_post(self):
        booking = self._make_booking(Booking.Status.COMPLETED, hours_from_now=-5)
        response = self.teacher_client.get(
            reverse('booking:update_lesson_note', kwargs={'booking_id': booking.id})
        )
        self.assertEqual(response.status_code, 405)

    # --- Lesson Note Modal Rendering ---

    def test_teacher_completed_booking_without_note_renders_add_note_controls(self):
        booking = self._make_booking(Booking.Status.COMPLETED, hours_from_now=-5)
        response = self.teacher_client.get(
            reverse('booking:lesson_detail', kwargs={'booking_id': booking.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '+ Add Lesson Note')
        self.assertContains(response, 'id="lessonNoteSection"')

    def test_teacher_completed_booking_with_note_renders_note_and_edit_controls(self):
        booking = self._make_booking(Booking.Status.COMPLETED, hours_from_now=-5)
        booking.completion_note = 'Practice speaking with audio clips.'
        booking.save()
        response = self.teacher_client.get(
            reverse('booking:lesson_detail', kwargs={'booking_id': booking.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Practice speaking with audio clips.')
        self.assertContains(response, 'Edit Note')
        self.assertContains(response, 'Lesson Note')

    def test_teacher_non_completed_booking_does_not_render_note_controls(self):
        booking = self._make_booking(Booking.Status.CONFIRMED, hours_from_now=5)
        response = self.teacher_client.get(
            reverse('booking:lesson_detail', kwargs={'booking_id': booking.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '+ Add Lesson Note')
        self.assertNotContains(response, 'Edit Note')

    def test_student_completed_booking_with_note_renders_read_only_note(self):
        booking = self._make_booking(Booking.Status.COMPLETED, hours_from_now=-5)
        booking.completion_note = 'Good job on grammar!'
        booking.save()
        response = self.student_client.get(
            reverse('booking:lesson_detail_student', kwargs={'booking_id': booking.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Good job on grammar!')
        self.assertContains(response, 'Lesson Note')
        self.assertNotContains(response, '+ Add Lesson Note')
        self.assertNotContains(response, 'Edit Note')
        self.assertNotContains(response, 'saveLessonNoteBtn')

    def test_student_completed_booking_without_note_omits_note_section(self):
        booking = self._make_booking(Booking.Status.COMPLETED, hours_from_now=-5)
        response = self.student_client.get(
            reverse('booking:lesson_detail_student', kwargs={'booking_id': booking.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Lesson Note')
        self.assertNotContains(response, 'lesson-detail-note')


class ReviewApprovalTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile
        past_start = timezone.now() - timedelta(days=1)
        self.booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=past_start,
            end_at=past_start + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )
        self.review = Review.objects.create(
            student=self.student_user,
            booking=self.booking,
            rating=5,
            comment='Great!',
            is_approved=False,
        )

    def test_approve_review_sets_is_approved_true(self):
        response = self.teacher_client.post(
            reverse('booking:approve_review', kwargs={'review_id': self.review.id})
        )
        self.assertEqual(response.status_code, 200)
        self.review.refresh_from_db()
        self.assertTrue(self.review.is_approved)

    def test_reject_review_deletes_it(self):
        response = self.teacher_client.post(
            reverse('booking:reject_review', kwargs={'review_id': self.review.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Review.objects.filter(id=self.review.id).exists())

    def test_teacher_cannot_approve_another_teachers_review(self):
        other_teacher = User.objects.create_user(
            username='other_t2', password='x', role=User.Role.TEACHER
        )
        other_booking = Booking.objects.create(
            student=self.student_user,
            teacher=other_teacher.teacher_profile,
            start_at=timezone.now() - timedelta(days=1),
            end_at=timezone.now() - timedelta(days=1) + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )
        other_review = Review.objects.create(
            student=self.student_user, booking=other_booking, rating=4, is_approved=False,
        )
        response = self.teacher_client.post(
            reverse('booking:approve_review', kwargs={'review_id': other_review.id})
        )
        self.assertEqual(response.status_code, 404)
        other_review.refresh_from_db()
        self.assertFalse(other_review.is_approved)


class SubmitReviewTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.teacher_user.teacher_profile
        past_start = timezone.now() - timedelta(days=1)
        self.completed_booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=past_start,
            end_at=past_start + timedelta(hours=1),
            status=Booking.Status.COMPLETED,
        )

    def test_submit_valid_review_succeeds(self):
        response = self.student_client.post(
            reverse('booking:submit_review', kwargs={'booking_id': self.completed_booking.id}),
            {'rating': 5, 'comment': 'Loved it'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Review.objects.filter(booking=self.completed_booking).exists())

    def test_submit_review_on_non_completed_booking_returns_404(self):
        future_start = timezone.now() + timedelta(days=1)
        confirmed_booking = Booking.objects.create(
            student=self.student_user,
            teacher=self.teacher,
            start_at=future_start,
            end_at=future_start + timedelta(hours=1),
            status=Booking.Status.CONFIRMED,
        )
        response = self.student_client.post(
            reverse('booking:submit_review', kwargs={'booking_id': confirmed_booking.id}),
            {'rating': 5, 'comment': 'Too early'},
        )
        self.assertEqual(response.status_code, 404)

    def test_submit_duplicate_review_returns_400(self):
        Review.objects.create(
            student=self.student_user, booking=self.completed_booking, rating=4,
        )
        response = self.student_client.post(
            reverse('booking:submit_review', kwargs={'booking_id': self.completed_booking.id}),
            {'rating': 5, 'comment': 'Second try'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Review.objects.filter(booking=self.completed_booking).count(), 1)