from django.core.management.base import BaseCommand
from accounts.models import User

class Command(BaseCommand):
    help = 'Creates the platform teacher account'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True)
        parser.add_argument('--email', required=True)
        parser.add_argument('--password', required=True)

    def handle(self, *args, **options):
        username = options['username']
        email = options['email']
        password = options['password']

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.ERROR(f'User "{username}" already exists.'))
            return

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            role=User.Role.TEACHER,
        )
        self.stdout.write(self.style.SUCCESS(f'Teacher account "{user.username}" created.'))