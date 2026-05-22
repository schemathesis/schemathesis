import pytest

pytest.importorskip("fastapi")
pytest.importorskip("starlette")

from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.routing import Route

from schemathesis.python._constants.adapters.fastapi import FastAPIAdapter


def _make_fastapi_app():
    app = FastAPI()

    @app.get("/items")
    def list_items():
        return []

    return app


def _make_starlette_app():
    def homepage(request):
        return None

    return Starlette(routes=[Route("/", endpoint=homepage)])


def test_matches_fastapi_app():
    assert FastAPIAdapter().matches(_make_fastapi_app()) is True


def test_matches_starlette_app():
    assert FastAPIAdapter().matches(_make_starlette_app()) is True


def test_does_not_match_non_app():
    assert FastAPIAdapter().matches(object()) is False


def test_handlers_from_fastapi():
    handlers = list(FastAPIAdapter().handlers(_make_fastapi_app()))
    assert any(h.__name__ == "list_items" for h in handlers)


def test_handlers_from_starlette():
    handlers = list(FastAPIAdapter().handlers(_make_starlette_app()))
    assert any(h.__name__ == "homepage" for h in handlers)
