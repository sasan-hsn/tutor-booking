from django.core.management.base import BaseCommand
from booking.services import expire_stale_bookings


class Command(BaseCommand):
    help = 'Expires all pending lesson requests whose scheduled start time has passed.'

    def handle(self, *args, **options):
        count = expire_stale_bookings()
        self.stdout.write(
            self.style.SUCCESS(f'Successfully expired {count} pending booking(s).')
        )
