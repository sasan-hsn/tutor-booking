from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse


def health_check(request):
    return HttpResponse("OK", status=200)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("portfolio.urls")),
    path('accounts/', include('accounts.urls')),
    path('dashboard/', include('booking.urls')),
    path('health/', health_check, name='health_check'),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
