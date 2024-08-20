import json
import sys
from base64 import b64decode

import pytest
import requests
from fastapi import FastAPI, Security
from fastapi.security import APIKeyHeader
from hypothesis import given, settings
from starlette_testclient import TestClient

import schemathesis
from schemathesis.generation import GenerationConfig
from schemathesis.models import Status
from schemathesis.runner import from_schema
from schemathesis.specs.openapi.checks import _contains_auth, _remove_auth_from_case, ignored_auth


def run(schema_url, headers=None, **loader_kwargs):
    schema = schemathesis.from_uri(schema_url, **loader_kwargs)
    _, _, _, _, _, _, event, *_ = from_schema(
        schema, checks=[ignored_auth], headers=headers, hypothesis_settings=settings(max_examples=1)
    ).execute()
    return event


@pytest.mark.parametrize("kwargs", ({}, {"generation_config": GenerationConfig(with_security_parameters=False)}))
@pytest.mark.operations("ignored_auth")
def test_auth_is_not_checked(schema_url, kwargs):
    # When auth is present (generated)
    # And endpoint declares auth as a requirement but doesn't actually require it
    event = run(schema_url, **kwargs)
    # Then it is a failure
    check = event.result.checks[-1]
    assert check.value == Status.failure
    assert check.name == "ignored_auth"
    # And the corresponding serialized case has no auth
    assert "Authorization" not in check.request.headers
    # And the reported response is the last one from the app
    assert json.loads(b64decode(check.response.body)) == {"has_auth": False}


@pytest.mark.operations("basic")
def test_auth_is_checked(schema_url):
    # When auth is present (generated)
    # And endpoint declares auth as a requirement and checks it
    event = run(schema_url, headers={"Authorization": "Basic dGVzdDp0ZXN0"})
    # Then there is no failure
    assert event.status == Status.success


@pytest.mark.operations("success")
def test_no_failure(schema_url):
    # When there is no auth
    event = run(schema_url)
    # Then there is no failure
    assert event.status == Status.success


@pytest.mark.parametrize(
    "request_kwargs, parameters, expected",
    (
        ({"url": "https://example.com", "headers": {"A": "V"}}, [{"name": "A", "in": "header"}], True),
        ({"url": "https://example.com", "headers": {"A": "V"}}, [{"name": "B", "in": "header"}], False),
        ({"url": "https://example.com?A=V"}, [{"name": "A", "in": "query"}], True),
        ({"url": "https://example.com?A=V"}, [{"name": "B", "in": "query"}], False),
        ({"url": "https://example.com", "cookies": {"A": "V"}}, [{"name": "A", "in": "cookie"}], True),
        ({"url": "https://example.com", "cookies": {"A": "V"}}, [{"name": "B", "in": "cookie"}], False),
    ),
)
def test_contains_auth(request_kwargs, parameters, expected):
    request = requests.Request("GET", **request_kwargs).prepare()
    assert _contains_auth(request, parameters) is expected


@pytest.mark.parametrize(
    "key, parameters",
    (
        ("headers", [{"name": "A", "in": "header"}]),
        ("query", [{"name": "A", "in": "query"}]),
        ("cookies", [{"name": "A", "in": "cookie"}]),
    ),
)
@pytest.mark.operations("success")
def test_remove_auth_from_case(schema_url, key, parameters):
    schema = schemathesis.from_uri(schema_url)
    case = schema["/success"]["GET"].make_case(**{key: {"A": "V"}})
    _remove_auth_from_case(case, parameters)
    assert not getattr(case, key)


@pytest.mark.skipif(sys.version_info < (3, 9), reason="typing.Annotated is not available in Python 3.8")
def test_proper_session():
    from typing import Annotated

    app = FastAPI()

    @app.get("/", responses={200: {"model": {}}, 403: {"model": {}}})
    def root(api_key: Annotated[str, Security(APIKeyHeader(name="x-api-key"))]):
        return {"message": "Hello world"}

    schemathesis.experimental.OPEN_API_3_1.enable()

    schema = schemathesis.from_asgi("/openapi.json", app)

    @given(case=schema["/"]["GET"].as_strategy())
    @settings(max_examples=10)
    def test(case):
        client = TestClient(app)
        case.call_and_validate(session=client)

    test()


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("ignored_auth")
def test_wsgi(wsgi_app_schema):
    _, _, _, _, _, _, event, *_ = from_schema(
        wsgi_app_schema, checks=[ignored_auth], hypothesis_settings=settings(max_examples=1)
    ).execute()
    check = event.result.checks[-1]
    assert check.value == Status.failure
    assert check.name == "ignored_auth"
    # And the corresponding serialized case has no auth
    assert "Authorization" not in check.request.headers
    # And the reported response is the last one from the app
    assert json.loads(b64decode(check.response.body)) == {"has_auth": False}
