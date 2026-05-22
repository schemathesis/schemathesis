import pytest

pytest.importorskip("django")

import django
from django.conf import settings
from django.urls import path

if not settings.configured:
    settings.configure(
        DEBUG=True,
        ROOT_URLCONF=__name__,
        SECRET_KEY="x",
        ALLOWED_HOSTS=["*"],
        DATABASES={},
        INSTALLED_APPS=[],
    )
    django.setup()


def home_view(request):
    return None


def detail_view(request, pk):
    return None


urlpatterns = [
    path("", home_view, name="home"),
    path("items/<int:pk>/", detail_view, name="detail"),
]


def _make_django_app():
    from django.core.handlers.wsgi import WSGIHandler

    return WSGIHandler()


from schemathesis.python._constants.adapters.django import DjangoAdapter  # noqa: E402


def test_matches_wsgi_handler():
    assert DjangoAdapter().matches(_make_django_app()) is True


def test_does_not_match_non_django():
    assert DjangoAdapter().matches(object()) is False


def test_handlers_includes_view_callables():
    handlers = list(DjangoAdapter().handlers(_make_django_app()))
    names = {h.__name__ for h in handlers}
    assert "home_view" in names
    assert "detail_view" in names
