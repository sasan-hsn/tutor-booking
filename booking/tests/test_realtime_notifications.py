from datetime import timedelta
from zoneinfo import ZoneInfo
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from booking.models import Booking, RegularAvailability
from booking.notifications import notify_booking_requested
from booking.services import expire_stale_bookings
from notifications.models import Notification
from portfolio.models import TeacherProfile
from tutor_booking.asgi import application

User = get_user_model()


@database_sync_to_async
def create_authenticated_session(user):
    session = SessionStore()
    session['_auth_user_id'] = str(user.pk)
    session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
    session['_auth_user_hash'] = user.get_session_auth_hash()
    session.save()
    return session.session_key


async def get_authenticated_communicator(app, path, user):
    session_key = await create_authenticated_session(user)
    headers = [
        (b"cookie", f"{settings.SESSION_COOKIE_NAME}={session_key}".encode("ascii")),
    ]
    return WebsocketCommunicator(app, path, headers=headers)


class BookingRealtimeNotificationIntegrationTests(TransactionTestCase):
    """
    End-to-end integration tests verifying that executing booking views with
    the Django test client emits the expected WebSocket frames to an authenticated
    WebsocketCommunicator and creates persistent database records.
    """

    def setUp(self):
        super().setUp()
        self.teacher_user = User.objects.create_user(
            username='mary_teacher',
            email='mary@englishwithmary.ir',
            password='password123',
            role=User.Role.TEACHER,
            first_name='Mary',
            last_name='Smith',
            timezone='Asia/Tehran',
            is_email_verified=True,
        )
        self.teacher = self.teacher_user.teacher_profile
        self.teacher.lesson_price = 25
        self.teacher.lesson_duration_minutes = 50
        self.teacher.offers_trial = True
        self.teacher.trial_price = 10
        self.teacher.trial_duration_minutes = 30
        self.teacher.headline = 'Experienced ESL Instructor'
        self.teacher.bio = 'Certified English teacher with 10+ years experience.'
        self.teacher.save()

        self.student_user = User.objects.create_user(
            username='john_student',
            email='john@example.com',
            password='password123',
            role=User.Role.STUDENT,
            first_name='John',
            last_name='Doe',
            timezone='America/New_York',
            is_email_verified=True,
        )

        # Setup regular availability for teacher across all days
        for day in range(7):
            RegularAvailability.objects.create(
                teacher=self.teacher,
                day_of_week=day,
                start_time=timezone.datetime.strptime("08:00", "%H:%M").time(),
                end_time=timezone.datetime.strptime("22:00", "%H:%M").time(),
            )

    async def test_book_slot_emits_booking_requested_notification_to_teacher(self):
        # Teacher connects to their authenticated WebSocket channel
        teacher_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.teacher_user)
        connected, _ = await teacher_ws.connect()
        self.assertTrue(connected)

        # Student logs in via test client and books a slot
        target_dt = timezone.now() + timedelta(days=2)
        teacher_tz = ZoneInfo(self.teacher_user.timezone)
        # Choose a clean hour (14:00 teacher local time)
        local_date = timezone.localtime(target_dt, teacher_tz).date()
        slot_dt = timezone.datetime.combine(local_date, timezone.datetime.strptime("14:00", "%H:%M").time(), tzinfo=teacher_tz)

        @database_sync_to_async
        def perform_booking():
            self.client.login(username='john_student', password='password123')
            response = self.client.post(
                reverse('booking:book_slot'),
                {
                    'start_at': slot_dt.isoformat(),
                    'lesson_option': 'trial',
                },
            )
            return response

        response = await perform_booking()
        self.assertEqual(response.status_code, 200)

        # Verify WebSocket frame received by teacher
        frame = await teacher_ws.receive_json_from()
        self.assertEqual(frame['type'], 'notification_message')
        self.assertEqual(frame['event_type'], Notification.EventType.BOOKING_REQUESTED)
        self.assertEqual(frame['level'], Notification.Level.INFO)
        self.assertEqual(frame['title'], 'New Lesson Request')
        self.assertEqual(frame['message'], 'John Doe requested a 25-minute Trial Lesson.')
        self.assertEqual(frame['action_url'], reverse('booking:teacher_dashboard'))
        self.assertEqual(frame['unread_count'], 1)

        # Verify persistent database record
        @database_sync_to_async
        def verify_db():
            notif = Notification.objects.get(id=frame['id'])
            self.assertEqual(notif.recipient, self.teacher_user)
            self.assertEqual(notif.actor, self.student_user)
            self.assertEqual(notif.event_type, Notification.EventType.BOOKING_REQUESTED)
            self.assertEqual(notif.message, 'John Doe requested a 25-minute Trial Lesson.')
            self.assertEqual(notif.metadata['lesson_duration'], 25)
            self.assertEqual(notif.metadata['participant_name'], 'John Doe')
            self.assertEqual(notif.metadata['start_time'], slot_dt.isoformat())
            self.assertEqual(notif.metadata['lesson_type'], 'trial')
            self.assertFalse(notif.is_read)

        await verify_db()
        await teacher_ws.disconnect()

    async def test_book_regular_slot_emits_booking_requested_notification_to_teacher(self):
        teacher_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.teacher_user)
        connected, _ = await teacher_ws.connect()
        self.assertTrue(connected)

        target_dt = timezone.now() + timedelta(days=2)
        teacher_tz = ZoneInfo(self.teacher_user.timezone)
        local_date = timezone.localtime(target_dt, teacher_tz).date()
        slot_dt = timezone.datetime.combine(local_date, timezone.datetime.strptime("16:00", "%H:%M").time(), tzinfo=teacher_tz)

        @database_sync_to_async
        def perform_booking():
            self.client.login(username='john_student', password='password123')
            response = self.client.post(
                reverse('booking:book_slot'),
                {
                    'start_at': slot_dt.isoformat(),
                    'lesson_option': 'regular_50',
                },
            )
            return response

        response = await perform_booking()
        self.assertEqual(response.status_code, 200)

        frame = await teacher_ws.receive_json_from()
        self.assertEqual(frame['type'], 'notification_message')
        self.assertEqual(frame['event_type'], Notification.EventType.BOOKING_REQUESTED)
        self.assertEqual(frame['message'], 'John Doe requested a 50-minute Regular Lesson.')

        @database_sync_to_async
        def verify_db():
            notif = Notification.objects.get(id=frame['id'])
            self.assertEqual(notif.message, 'John Doe requested a 50-minute Regular Lesson.')
            self.assertEqual(notif.metadata['lesson_type'], 'regular')

        await verify_db()
        await teacher_ws.disconnect()

    async def test_accept_booking_emits_booking_confirmed_notification_to_student(self):
        # Create a pending booking
        target_dt = timezone.now() + timedelta(days=2)
        booking = await Booking.objects.acreate(
            student=self.student_user,
            teacher=self.teacher,
            start_at=target_dt,
            end_at=target_dt + timedelta(minutes=50),
            duration_minutes=50,
            status=Booking.Status.PENDING,
        )

        student_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.student_user)
        connected, _ = await student_ws.connect()
        self.assertTrue(connected)

        @database_sync_to_async
        def perform_accept():
            self.client.login(username='mary_teacher', password='password123')
            response = self.client.post(
                reverse('booking:respond_to_booking', kwargs={'booking_id': booking.id}),
                {'action': 'accept'},
            )
            return response

        response = await perform_accept()
        self.assertEqual(response.status_code, 200)

        frame = await student_ws.receive_json_from()
        self.assertEqual(frame['type'], 'notification_message')
        self.assertEqual(frame['event_type'], Notification.EventType.BOOKING_CONFIRMED)
        self.assertEqual(frame['level'], Notification.Level.SUCCESS)
        self.assertEqual(frame['title'], 'Lesson Confirmed')
        self.assertEqual(frame['message'], 'Your Regular Lesson with Mary Smith has been confirmed.')
        self.assertEqual(frame['action_url'], reverse('booking:student_dashboard'))
        self.assertEqual(frame['unread_count'], 1)

        @database_sync_to_async
        def verify_db():
            notif = Notification.objects.get(id=frame['id'])
            self.assertEqual(notif.recipient, self.student_user)
            self.assertEqual(notif.actor, self.teacher_user)
            self.assertEqual(notif.event_type, Notification.EventType.BOOKING_CONFIRMED)
            self.assertEqual(notif.metadata['booking_id'], booking.id)
            self.assertEqual(notif.metadata['lesson_duration'], 50)
            self.assertEqual(notif.metadata['participant_name'], 'Mary Smith')

        await verify_db()
        await student_ws.disconnect()

    async def test_decline_booking_emits_booking_declined_notification_to_student(self):
        target_dt = timezone.now() + timedelta(days=2)
        booking = await Booking.objects.acreate(
            student=self.student_user,
            teacher=self.teacher,
            start_at=target_dt,
            end_at=target_dt + timedelta(minutes=50),
            duration_minutes=50,
            status=Booking.Status.PENDING,
        )

        student_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.student_user)
        connected, _ = await student_ws.connect()
        self.assertTrue(connected)

        @database_sync_to_async
        def perform_decline():
            self.client.login(username='mary_teacher', password='password123')
            response = self.client.post(
                reverse('booking:respond_to_booking', kwargs={'booking_id': booking.id}),
                {'action': 'decline'},
            )
            return response

        response = await perform_decline()
        self.assertEqual(response.status_code, 200)

        frame = await student_ws.receive_json_from()
        self.assertEqual(frame['type'], 'notification_message')
        self.assertEqual(frame['event_type'], Notification.EventType.BOOKING_DECLINED)
        self.assertEqual(frame['level'], Notification.Level.WARNING)
        self.assertEqual(frame['title'], 'Lesson Request Declined')
        self.assertEqual(frame['message'], 'Mary Smith was unable to accept your Regular Lesson request.')
        self.assertEqual(frame['action_url'], reverse('booking:student_dashboard'))

        @database_sync_to_async
        def verify_db():
            notif = Notification.objects.get(id=frame['id'])
            self.assertEqual(notif.recipient, self.student_user)
            self.assertEqual(notif.actor, self.teacher_user)
            self.assertEqual(notif.event_type, Notification.EventType.BOOKING_DECLINED)
            self.assertEqual(notif.metadata['booking_id'], booking.id)

        await verify_db()
        await student_ws.disconnect()

    async def test_request_cancellation_emits_cancellation_requested_to_teacher(self):
        target_dt = timezone.now() + timedelta(days=2)
        booking = await Booking.objects.acreate(
            student=self.student_user,
            teacher=self.teacher,
            start_at=target_dt,
            end_at=target_dt + timedelta(minutes=50),
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
        )

        teacher_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.teacher_user)
        connected, _ = await teacher_ws.connect()
        self.assertTrue(connected)

        @database_sync_to_async
        def perform_request_cancellation():
            self.client.login(username='john_student', password='password123')
            response = self.client.post(
                reverse('booking:request_cancellation', kwargs={'booking_id': booking.id}),
            )
            return response

        response = await perform_request_cancellation()
        self.assertEqual(response.status_code, 200)

        frame = await teacher_ws.receive_json_from()
        self.assertEqual(frame['type'], 'notification_message')
        self.assertEqual(frame['event_type'], Notification.EventType.CANCELLATION_REQUESTED)
        self.assertEqual(frame['level'], Notification.Level.WARNING)
        self.assertEqual(frame['title'], 'Cancellation Requested')
        self.assertEqual(frame['message'], 'John Doe requested to cancel their upcoming Regular Lesson.')
        self.assertEqual(frame['action_url'], reverse('booking:teacher_dashboard'))

        @database_sync_to_async
        def verify_db():
            notif = Notification.objects.get(id=frame['id'])
            self.assertEqual(notif.recipient, self.teacher_user)
            self.assertEqual(notif.actor, self.student_user)
            self.assertEqual(notif.event_type, Notification.EventType.CANCELLATION_REQUESTED)
            self.assertEqual(notif.metadata['participant_name'], 'John Doe')

        await verify_db()
        await teacher_ws.disconnect()

    async def test_cancel_lesson_by_teacher_emits_booking_cancelled_to_student(self):
        target_dt = timezone.now() + timedelta(days=2)
        booking = await Booking.objects.acreate(
            student=self.student_user,
            teacher=self.teacher,
            start_at=target_dt,
            end_at=target_dt + timedelta(minutes=50),
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
        )

        student_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.student_user)
        connected, _ = await student_ws.connect()
        self.assertTrue(connected)

        @database_sync_to_async
        def perform_cancel_lesson():
            self.client.login(username='mary_teacher', password='password123')
            response = self.client.post(
                reverse('booking:cancel_lesson', kwargs={'booking_id': booking.id}),
            )
            return response

        response = await perform_cancel_lesson()
        self.assertEqual(response.status_code, 200)

        frame = await student_ws.receive_json_from()
        self.assertEqual(frame['type'], 'notification_message')
        self.assertEqual(frame['event_type'], Notification.EventType.BOOKING_CANCELLED)
        self.assertEqual(frame['level'], Notification.Level.WARNING)
        self.assertEqual(frame['title'], 'Lesson Cancelled')
        self.assertEqual(frame['message'], 'Your Regular Lesson with Mary Smith has been cancelled.')
        self.assertEqual(frame['action_url'], reverse('booking:student_dashboard'))

        @database_sync_to_async
        def verify_db():
            notif = Notification.objects.get(id=frame['id'])
            self.assertEqual(notif.recipient, self.student_user)
            self.assertEqual(notif.actor, self.teacher_user)
            self.assertEqual(notif.event_type, Notification.EventType.BOOKING_CANCELLED)
            self.assertEqual(notif.metadata['participant_name'], 'Mary Smith')

        await verify_db()
        await student_ws.disconnect()

    async def test_teacher_accepting_cancellation_request_emits_booking_cancelled_to_student(self):
        target_dt = timezone.now() + timedelta(days=2)
        booking = await Booking.objects.acreate(
            student=self.student_user,
            teacher=self.teacher,
            start_at=target_dt,
            end_at=target_dt + timedelta(minutes=50),
            duration_minutes=50,
            status=Booking.Status.CONFIRMED,
            cancellation_requested=True,
        )

        student_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.student_user)
        connected, _ = await student_ws.connect()
        self.assertTrue(connected)

        @database_sync_to_async
        def perform_accept_cancellation():
            self.client.login(username='mary_teacher', password='password123')
            response = self.client.post(
                reverse('booking:respond_to_booking', kwargs={'booking_id': booking.id}),
                {'action': 'accept'},
            )
            return response

        response = await perform_accept_cancellation()
        self.assertEqual(response.status_code, 200)

        frame = await student_ws.receive_json_from()
        self.assertEqual(frame['type'], 'notification_message')
        self.assertEqual(frame['event_type'], Notification.EventType.BOOKING_CANCELLED)
        self.assertEqual(frame['title'], 'Lesson Cancelled')
        self.assertEqual(frame['message'], 'Your Regular Lesson with Mary Smith has been cancelled.')

        await student_ws.disconnect()

    async def test_expire_stale_bookings_emits_booking_expired_to_both_student_and_teacher(self):
        past_dt = timezone.now() - timedelta(hours=2)
        booking = await Booking.objects.acreate(
            student=self.student_user,
            teacher=self.teacher,
            start_at=past_dt,
            end_at=past_dt + timedelta(minutes=50),
            duration_minutes=50,
            status=Booking.Status.PENDING,
        )

        student_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.student_user)
        student_connected, _ = await student_ws.connect()
        self.assertTrue(student_connected)

        teacher_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.teacher_user)
        teacher_connected, _ = await teacher_ws.connect()
        self.assertTrue(teacher_connected)

        @database_sync_to_async
        def run_expire():
            return expire_stale_bookings()

        count = await run_expire()
        self.assertEqual(count, 1)

        # Student receives booking_expired frame
        student_frame = await student_ws.receive_json_from()
        self.assertEqual(student_frame['type'], 'notification_message')
        self.assertEqual(student_frame['event_type'], Notification.EventType.BOOKING_EXPIRED)
        self.assertEqual(student_frame['level'], Notification.Level.WARNING)
        self.assertEqual(student_frame['title'], 'Lesson Request Expired')
        self.assertEqual(student_frame['message'], 'Your Regular Lesson request with Mary Smith has expired.')
        self.assertEqual(student_frame['action_url'], reverse('booking:student_dashboard'))

        # Teacher receives booking_expired frame
        teacher_frame = await teacher_ws.receive_json_from()
        self.assertEqual(teacher_frame['type'], 'notification_message')
        self.assertEqual(teacher_frame['event_type'], Notification.EventType.BOOKING_EXPIRED)
        self.assertEqual(teacher_frame['level'], Notification.Level.WARNING)
        self.assertEqual(teacher_frame['title'], 'Lesson Request Expired')
        self.assertEqual(teacher_frame['message'], 'The Regular Lesson request from John Doe has expired.')
        self.assertEqual(teacher_frame['action_url'], reverse('booking:teacher_dashboard'))

        # Verify persistent records
        @database_sync_to_async
        def verify_db():
            student_notif = Notification.objects.get(id=student_frame['id'])
            self.assertEqual(student_notif.recipient, self.student_user)
            self.assertEqual(student_notif.event_type, Notification.EventType.BOOKING_EXPIRED)
            self.assertEqual(student_notif.metadata['participant_name'], 'Mary Smith')

            teacher_notif = Notification.objects.get(id=teacher_frame['id'])
            self.assertEqual(teacher_notif.recipient, self.teacher_user)
            self.assertEqual(teacher_notif.event_type, Notification.EventType.BOOKING_EXPIRED)
            self.assertEqual(teacher_notif.metadata['participant_name'], 'John Doe')

        await verify_db()

        await student_ws.disconnect()
        await teacher_ws.disconnect()

    async def test_respond_to_expired_booking_in_view_emits_booking_expired_notifications(self):
        past_dt = timezone.now() - timedelta(hours=2)
        booking = await Booking.objects.acreate(
            student=self.student_user,
            teacher=self.teacher,
            start_at=past_dt,
            end_at=past_dt + timedelta(minutes=50),
            duration_minutes=50,
            status=Booking.Status.PENDING,
        )

        student_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.student_user)
        student_connected, _ = await student_ws.connect()
        self.assertTrue(student_connected)

        teacher_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.teacher_user)
        teacher_connected, _ = await teacher_ws.connect()
        self.assertTrue(teacher_connected)

        @database_sync_to_async
        def perform_respond():
            self.client.login(username='mary_teacher', password='password123')
            response = self.client.post(
                reverse('booking:respond_to_booking', kwargs={'booking_id': booking.id}),
                {'action': 'accept'},
            )
            return response

        response = await perform_respond()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'This lesson request has expired.'})

        # Student receives booking_expired frame
        student_frame = await student_ws.receive_json_from()
        self.assertEqual(student_frame['type'], 'notification_message')
        self.assertEqual(student_frame['event_type'], Notification.EventType.BOOKING_EXPIRED)

        # Teacher receives booking_expired frame
        teacher_frame = await teacher_ws.receive_json_from()
        self.assertEqual(teacher_frame['type'], 'notification_message')
        self.assertEqual(teacher_frame['event_type'], Notification.EventType.BOOKING_EXPIRED)

        await student_ws.disconnect()
        await teacher_ws.disconnect()

    async def test_dashboard_view_request_expires_stale_bookings_and_emits_websocket_frames(self):
        past_dt = timezone.now() - timedelta(hours=3)
        booking = await Booking.objects.acreate(
            student=self.student_user,
            teacher=self.teacher,
            start_at=past_dt,
            end_at=past_dt + timedelta(minutes=50),
            duration_minutes=50,
            status=Booking.Status.PENDING,
        )

        student_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.student_user)
        student_connected, _ = await student_ws.connect()
        self.assertTrue(student_connected)

        teacher_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.teacher_user)
        teacher_connected, _ = await teacher_ws.connect()
        self.assertTrue(teacher_connected)

        # Access teacher_dashboard via test client, which calls expire_stale_bookings(teacher=teacher)
        @database_sync_to_async
        def visit_dashboard():
            self.client.login(username='mary_teacher', password='password123')
            return self.client.get(reverse('booking:teacher_dashboard'))

        response = await visit_dashboard()
        self.assertEqual(response.status_code, 200)

        # Verify both student and teacher received real-time frames via WebSocket
        student_frame = await student_ws.receive_json_from()
        self.assertEqual(student_frame['type'], 'notification_message')
        self.assertEqual(student_frame['event_type'], Notification.EventType.BOOKING_EXPIRED)

        teacher_frame = await teacher_ws.receive_json_from()
        self.assertEqual(teacher_frame['type'], 'notification_message')
        self.assertEqual(teacher_frame['event_type'], Notification.EventType.BOOKING_EXPIRED)

        await student_ws.disconnect()
        await teacher_ws.disconnect()

    async def test_respond_to_booking_broadcasts_notification_read_frame_to_teacher(self):
        target_dt = timezone.now() + timedelta(days=2)
        booking = await Booking.objects.acreate(
            student=self.student_user,
            teacher=self.teacher,
            start_at=target_dt,
            end_at=target_dt + timedelta(minutes=50),
            duration_minutes=50,
            status=Booking.Status.PENDING,
        )

        @database_sync_to_async
        def create_notification():
            return notify_booking_requested(booking)

        notif = await create_notification()
        self.assertIsNotNone(notif)

        teacher_ws = await get_authenticated_communicator(application, "/ws/notifications/", self.teacher_user)
        connected, _ = await teacher_ws.connect()
        self.assertTrue(connected)

        @database_sync_to_async
        def perform_accept():
            self.client.login(username='mary_teacher', password='password123')
            return self.client.post(
                reverse('booking:respond_to_booking', kwargs={'booking_id': booking.id}),
                {'action': 'accept'},
            )

        response = await perform_accept()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn(notif.id, data['marked_notification_ids'])
        self.assertEqual(data['unread_notifications_count'], 0)

        # Teacher receives notification_read frame
        read_frame = await teacher_ws.receive_json_from()
        self.assertEqual(read_frame['type'], 'notification_read')
        self.assertIn(notif.id, read_frame['notification_ids'])
        self.assertEqual(read_frame['unread_count'], 0)

        await teacher_ws.disconnect()


