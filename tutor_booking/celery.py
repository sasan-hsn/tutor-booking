import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tutor_booking.settings')

app = Celery('tutor_booking')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
