import json

from django.conf import settings

if not settings.configured:
    settings.configure(DEBUG=True, ROOT_URLCONF=__name__, ALLOWED_HOSTS=["*"], SECRET_KEY="schemathesis-test")

import django  # noqa: E402

django.setup()

from django.core.handlers.wsgi import WSGIHandler  # noqa: E402
from django.http import HttpResponse  # noqa: E402
from django.urls import path  # noqa: E402

DJANGO_UNLOCK_CODE = "dj7a3f9c1e7b5d24"


def unlock(request):
    try:
        data = json.loads(request.body)
    except ValueError:
        data = None
    if isinstance(data, dict) and data.get("code") == DJANGO_UNLOCK_CODE:
        return HttpResponse(status=500)
    return HttpResponse("ok")


urlpatterns = [path("unlock", unlock)]


def make_app():
    return WSGIHandler()
