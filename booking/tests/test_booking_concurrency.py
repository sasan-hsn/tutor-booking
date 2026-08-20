import threading
from django.test import TransactionTestCase, Client
from django.urls import reverse
from django.db import connection
from datetime import date, time
from accounts.models import User
from booking.models import RegularAvailability, Booking


class DoubleBookingEndpointConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username='testteacher', password='test123', role=User.Role.TEACHER
        )
        self.teacher = self.teacher_user.teacher_profile

        self.student1 = User.objects.create_user(
            username='student1', password='test123', role=User.Role.STUDENT
        )
        self.student2 = User.objects.create_user(
            username='student2', password='test123', role=User.Role.STUDENT
        )

        # 2026-09-28 is a Monday
        self.booking_date = date(2026, 9, 28)

        RegularAvailability.objects.create(
            teacher=self.teacher,
            day_of_week=RegularAvailability.DayOfWeek.MONDAY,
            start_time=time(10, 0),
            end_time=time(11, 0),
        )

        self.book_url = reverse('booking:book_slot')

    def test_concurrent_requests_to_endpoint_only_one_succeeds(self):
        """Two real HTTP POST requests hitting book_slot at the same time — only one should succeed."""
        status_codes = []

        def make_request(username):
            client = Client()
            client.login(username=username, password='test123')
            response = client.post(self.book_url, {
                'date': self.booking_date.isoformat(),
                'start_time': '10:00',
            })
            status_codes.append(response.status_code)
            connection.close()

        thread1 = threading.Thread(target=make_request, args=('student1',))
        thread2 = threading.Thread(target=make_request, args=('student2',))

        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()

        self.assertIn(200, status_codes)
        self.assertIn(409, status_codes)
        self.assertEqual(
            Booking.objects.filter(
                teacher=self.teacher,
                date=self.booking_date,
                status__in=[Booking.Status.PENDING, Booking.Status.CONFIRMED],
            ).count(),
            1,
        )