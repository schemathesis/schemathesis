import json
import sys
from base64 import b64decode
from unittest.mock import Mock

import pytest
import requests
from fastapi import FastAPI, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials
from hypothesis import Phase, given, settings
from starlette_testclient import TestClient

import schemathesis
from schemathesis.exceptions import CheckFailed
from schemathesis.generation import GenerationConfig
from schemathesis.internal.checks import CheckContext
from schemathesis.models import Status
from schemathesis.runner import from_schema
from schemathesis.specs.openapi.checks import AuthKind, _contains_auth, _remove_auth_from_case, ignored_auth


def run(schema_url, headers=None, **loader_kwargs):
    schema = schemathesis.from_uri(schema_url, **loader_kwargs)
    _, _, _, _, _, _, event, *_ = from_schema(
        schema,
        checks=[ignored_auth],
        headers=headers,
        hypothesis_settings=settings(max_examples=1, phases=[Phase.generate]),
    ).execute()
    return event


@pytest.mark.parametrize("with_generated", [True, False])
@pytest.mark.operations("ignored_auth")
def test_auth_is_not_checked(with_generated, schema_url):
    kwargs = {}
    if not with_generated:
        kwargs["generation_config"] = GenerationConfig(with_security_parameters=False)
    # When auth is present
    # And endpoint declares auth as a requirement but doesn't actually require it
    event = run(schema_url, **kwargs)
    # Then it is a failure
    check = event.result.checks[-1]
    assert check.value == Status.failure
    assert check.name == "ignored_auth"
    if with_generated:
        assert "Authorization" in check.request.headers
        assert json.loads(b64decode(check.response.body)) == {"has_auth": True}
    else:
        assert "Authorization" not in check.request.headers
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
    "ctx, request_kwargs, parameters, expected",
    (
        (
            CheckContext(headers=None),
            {"url": "https://example.com", "headers": {"A": "V"}},
            [{"name": "A", "in": "header"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(headers={"Foo": "Bar"}),
            {"url": "https://example.com", "headers": {"A": "V"}},
            [{"name": "A", "in": "header"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(headers={"A": "V"}),
            {"url": "https://example.com", "headers": {"A": "V"}},
            [{"name": "A", "in": "header"}],
            AuthKind.EXPLICIT,
        ),
        (
            CheckContext(headers={}),
            {"url": "https://example.com", "headers": {"A": "V"}},
            [{"name": "B", "in": "header"}],
            None,
        ),
        (
            CheckContext(headers={}),
            {"url": "https://example.com?A=V"},
            [{"name": "A", "in": "query"}],
            AuthKind.GENERATED,
        ),
        (CheckContext(headers={}), {"url": "https://example.com?A=V"}, [{"name": "B", "in": "query"}], None),
        (
            CheckContext(headers={}),
            {"url": "https://example.com", "cookies": {"A": "V"}},
            [{"name": "A", "in": "cookie"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(headers={"Cookie": "A=v;"}),
            {"url": "https://example.com", "cookies": {"A": "V"}},
            [{"name": "A", "in": "cookie"}],
            AuthKind.EXPLICIT,
        ),
        (
            CheckContext(headers={"Cookie": "B=v;"}),
            {"url": "https://example.com", "cookies": {"A": "V"}},
            [{"name": "A", "in": "cookie"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(headers={}),
            {"url": "https://example.com", "cookies": {"A": "V"}},
            [{"name": "B", "in": "cookie"}],
            None,
        ),
    ),
)
def test_contains_auth(ctx, request_kwargs, parameters, expected):
    request = requests.Request("GET", **request_kwargs).prepare()
    assert _contains_auth(ctx, Mock(_has_explicit_auth=False), request, parameters) == expected


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


@pytest.mark.parametrize("ignores_auth", [True, False])
@pytest.mark.skipif(sys.version_info < (3, 9), reason="typing.Annotated is not available in Python 3.8")
def test_proper_session(ignores_auth):
    from typing import Annotated

    app = FastAPI()

    @app.get("/", responses={200: {"model": {}}, 401: {"model": {}}, 403: {"model": {}}})
    def root(api_key: Annotated[str, Security(APIKeyHeader(name="x-api-key"))]):
        if not ignores_auth and api_key != "super-secret":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect key")

        return {"message": "Hello world"}

    schemathesis.experimental.OPEN_API_3_1.enable()

    schema = schemathesis.from_asgi("/openapi.json", app)

    @given(case=schema["/"]["GET"].as_strategy())
    @settings(max_examples=3, phases=[Phase.generate])
    def test(case):
        client = TestClient(app)
        case.call_and_validate(session=client)

    if ignores_auth:
        with pytest.raises(CheckFailed, match="with invalid auth"):
            test()
    else:
        test()


@pytest.mark.parametrize("ignores_auth", [True, False])
@pytest.mark.skipif(sys.version_info < (3, 10), reason="Typing syntax is not supported on Python 3.9 and below")
def test_accepts_any_auth_if_explicit_is_present(ignores_auth):
    app = FastAPI()

    @app.get("/", responses={200: {"model": {}}, 401: {"model": {}}, 403: {"model": {}}})
    async def root(
        credentials: HTTPAuthorizationCredentials | None = Security(APIKeyHeader(name="x-api-key", auto_error=False)),
    ):
        # Accept any auth, but raise an error if Authorization header is missing
        if ignores_auth and credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization header is missing",
            )
        return {"message": "Hello world"}

    schemathesis.experimental.OPEN_API_3_1.enable()

    schema = schemathesis.from_asgi("/openapi.json", app)

    @given(case=schema["/"]["GET"].as_strategy())
    @settings(max_examples=3, phases=[Phase.generate])
    def test(case):
        client = TestClient(app)
        case.call_and_validate(session=client, headers={"x-api-key": "INCORRECT"})

    if ignores_auth:
        matches = "with any auth"
    else:
        matches = "that requires authentication"
    with pytest.raises(CheckFailed, match=matches):
        test()


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Foo"}])
@pytest.mark.parametrize("with_generated", [True, False])
@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("ignored_auth")
def test_wsgi(wsgi_app_schema, with_generated, headers):
    kwargs = {"headers": headers}
    if not with_generated:
        kwargs["generation_config"] = GenerationConfig(with_security_parameters=False)
    _, _, _, _, _, _, event, *_ = from_schema(
        wsgi_app_schema, checks=[ignored_auth], hypothesis_settings=settings(max_examples=1), **kwargs
    ).execute()
    check = event.result.checks[-1]
    assert check.value == Status.failure
    assert check.name == "ignored_auth"
    if with_generated and not headers:
        assert "Authorization" in check.request.headers
        assert json.loads(b64decode(check.response.body)) == {"has_auth": True}
    else:
        assert "Authorization" not in check.request.headers
        assert json.loads(b64decode(check.response.body)) == {"has_auth": False}


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("ignored_auth")
def test_explicit_auth(wsgi_app_schema):
    kwargs = {"auth": ("foo", "bar")}
    _, _, _, _, _, _, event, *_ = from_schema(
        wsgi_app_schema, checks=[ignored_auth], hypothesis_settings=settings(max_examples=1), **kwargs
    ).execute()
    check = event.result.checks[-1]
    assert check.value == Status.failure
    assert check.name == "ignored_auth"


@pytest.mark.skipif(sys.version_info < (3, 10), reason="Typing syntax is not supported on Python 3.9 and below")
def test_custom_auth():
    app = FastAPI()
    token = "TEST"

    @app.get("/", responses={200: {"model": {}}, 401: {"model": {}}, 403: {"model": {}}})
    async def root(
        credentials: HTTPAuthorizationCredentials = Security(APIKeyHeader(name="x-api-key")),
    ):
        if credentials != token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        return {"message": "Hello world"}

    schemathesis.experimental.OPEN_API_3_1.enable()

    schema = schemathesis.from_asgi("/openapi.json", app)

    @schema.auth()
    class Auth:
        def get(self, case, context):
            return token

        def set(self, case, data, context):
            case.headers = case.headers or {}
            case.headers["x-api-key"] = data

    @given(case=schema["/"]["GET"].as_strategy())
    @settings(max_examples=10)
    def test(case):
        client = TestClient(app)
        case.call_and_validate(session=client)

    test()
