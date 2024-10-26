"""Tests for common schema loading logic shared by all loaders."""

import json
import platform
from contextlib import suppress

import pytest
from requests import Response
from yarl import URL

import schemathesis
from schemathesis.constants import USER_AGENT
from schemathesis.exceptions import SchemaError


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
    "loader", [schemathesis.openapi.from_dict, schemathesis.openapi.from_pytest_fixture, schemathesis.graphql.from_dict]
)
def test_invalid_code_sample_style(loader):
    with pytest.raises(ValueError, match="Invalid value for code sample style: ruby. Available styles: python, curl"):
        loader({}, code_sample_style="ruby")


@pytest.fixture
def default_schema_url():
    return "http://127.0.0.1/schema.yaml"


@pytest.mark.parametrize(
    ("loader", "url_fixture", "expected"),
    [
        (schemathesis.openapi.from_uri, "openapi3_schema_url", "http://127.0.0.1:8081/schema.yaml"),
        (schemathesis.openapi.from_uri, "default_schema_url", "http://127.0.0.1:8081/schema.yaml"),
        (schemathesis.graphql.from_url, "graphql_url", "http://127.0.0.1:8081/graphql"),
    ],
)
def test_port_override(request, loader, url_fixture, expected):
    url = request.getfixturevalue(url_fixture)
    # When the user overrides `port`
    with pytest.raises(SchemaError) as exc:
        loader(url, port=8081)
    if platform.system() == "Windows":
        detail = "[WinError 10061] No connection could be made because the target machine actively refused it"
    elif platform.system() == "Darwin":
        detail = "[Errno 61] Connection refused"
    else:
        detail = "[Errno 111] Connection refused"
    assert exc.value.extras == [f"Failed to establish a new connection: {detail}"]


def to_ipv6(url):
    url = URL(url)
    parts = list(map(int, url.host.split(".")))
    ipv6_host = "2002:{:02x}{:02x}:{:02x}{:02x}::".format(*parts)
    return str(url.with_host(ipv6_host))


@pytest.mark.parametrize(
    ("loader", "url_fixture", "target", "expected"),
    [
        (
            schemathesis.openapi.from_uri,
            "openapi3_schema_url",
            "requests.get",
            "http://[2002:7f00:1::]:8081/schema.yaml",
        ),
        (
            schemathesis.graphql.from_url,
            "graphql_url",
            "requests.post",
            "http://[2002:7f00:1::]:8081/graphql",
        ),
    ],
)
def test_port_override_with_ipv6(request, loader, url_fixture, target, mocker, expected):
    url = request.getfixturevalue(url_fixture)
    raw_schema = loader(url).raw_schema
    response = Response()
    response.status_code = 200
    response._content = json.dumps({"data": raw_schema} if url_fixture == "graphql_url" else raw_schema).encode("utf-8")
    mocker.patch(target, return_value=response)

    url = to_ipv6(url)
    schema = loader(url, validate_schema=False, port=8081)
    operation = next(schema.get_all_operations()).ok()
    assert operation.base_url == expected


@pytest.mark.parametrize(
    ("loader", "url_fixture"),
    [
        (schemathesis.openapi.from_uri, "openapi3_schema_url"),
        (schemathesis.graphql.from_url, "graphql_url"),
    ],
)
@pytest.mark.parametrize("base_url", ["http://example.com/", "http://example.com"])
def test_base_url_override(request, loader, url_fixture, base_url):
    url = request.getfixturevalue(url_fixture)
    # When the user overrides base_url
    schema = loader(url, base_url=base_url)
    operation = next(schema.get_all_operations()).ok()
    # Then the overridden value should not have a trailing slash
    assert operation.base_url == "http://example.com"


@pytest.mark.parametrize(
    ("target", "loader"),
    [
        ("requests.get", schemathesis.openapi.from_uri),
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
    schemathesis.openapi.from_uri(schema_url, auth=("test", "test"))
    assert app["schema_requests"][0].headers["Authorization"] == "Basic dGVzdDp0ZXN0"
