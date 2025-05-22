"""Tests for common schema loading logic shared by all loaders."""

from contextlib import suppress

import pytest

import schemathesis
from schemathesis.core.transport import USER_AGENT


@pytest.mark.parametrize(
    "loader",
    [
        schemathesis.openapi.from_asgi,
        schemathesis.openapi.from_wsgi,
        schemathesis.graphql.from_asgi,
        schemathesis.graphql.from_wsgi,
    ],
)
def test_absolute_urls_for_apps(loader):
    # When an absolute URL passed to a ASGI / WSGI loader
    # Then it should be rejected
    with pytest.raises(ValueError, match="Schema path should be relative for WSGI/ASGI loaders"):
        loader("http://127.0.0.1:1/schema.json", app=None)  # actual app doesn't matter here


@pytest.mark.parametrize(
    ("loader", "url_fixture"),
    [
        (schemathesis.openapi.from_url, "openapi3_schema_url"),
        (schemathesis.graphql.from_url, "graphql_url"),
    ],
)
@pytest.mark.parametrize("base_url", ["http://example.com/", "http://example.com"])
def test_base_url_override(request, loader, url_fixture, base_url):
    url = request.getfixturevalue(url_fixture)
    # When the user overrides base_url
    schema = loader(url)
    schema.config.update(base_url=base_url)
    operation = next(schema.get_all_operations()).ok()
    # Then the overridden value should not have a trailing slash
    assert operation.base_url == "http://example.com"


@pytest.mark.parametrize(
    ("target", "loader"),
    [
        ("requests.get", schemathesis.openapi.from_url),
        ("requests.post", schemathesis.graphql.from_url),
    ],
)
def test_uri_loader_custom_kwargs(mocker, target, loader):
    # All custom kwargs are passed to `requests` as is
    mocked = mocker.patch(target)
    with suppress(Exception):
        loader("http://127.0.0.1:8000", verify=False, headers={"X-Test": "foo"})
    assert mocked.call_args[1]["verify"] is False
    assert mocked.call_args[1]["headers"] == {"X-Test": "foo", "User-Agent": USER_AGENT}


def test_auth_loader_options(schema_url, app):
    schemathesis.openapi.from_url(schema_url, auth=("test", "test"))
    assert app["schema_requests"][0].headers["Authorization"] == "Basic dGVzdDp0ZXN0"
