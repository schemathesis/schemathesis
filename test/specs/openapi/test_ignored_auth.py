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
    ("ctx", "request_kwargs", "parameters", "expected"),
    [
        (
            CheckContext(override=None, auth=None, headers=None),
            {"url": "https://example.com", "headers": {"A": "V"}},
            [{"name": "A", "in": "header"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(override=None, auth=None, headers={"Foo": "Bar"}),
            {"url": "https://example.com", "headers": {"A": "V"}},
            [{"name": "A", "in": "header"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(override=None, auth=None, headers={"A": "V"}),
            {"url": "https://example.com", "headers": {"A": "V"}},
            [{"name": "A", "in": "header"}],
            AuthKind.EXPLICIT,
        ),
        (
            CheckContext(override=None, auth=None, headers={}),
            {"url": "https://example.com", "headers": {"A": "V"}},
            [{"name": "B", "in": "header"}],
            None,
        ),
        (
            CheckContext(override=None, auth=None, headers={}),
            {"url": "https://example.com?A=V"},
            [{"name": "A", "in": "query"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(override=None, auth=None, headers={}),
            {"url": "https://example.com?A=V"},
            [{"name": "B", "in": "query"}],
            None,
        ),
        (
            CheckContext(override=None, auth=None, headers={}),
            {"url": "https://example.com", "cookies": {"A": "V"}},
            [{"name": "A", "in": "cookie"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(override=None, auth=None, headers={"Cookie": "A=v;"}),
            {"url": "https://example.com", "cookies": {"A": "V"}},
            [{"name": "A", "in": "cookie"}],
            AuthKind.EXPLICIT,
        ),
        (
            CheckContext(override=None, auth=None, headers={"Cookie": "B=v;"}),
            {"url": "https://example.com", "cookies": {"A": "V"}},
            [{"name": "A", "in": "cookie"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(override=None, auth=None, headers={}),
            {"url": "https://example.com", "cookies": {"A": "V"}},
            [{"name": "B", "in": "cookie"}],
            None,
        ),
    ],
)
def test_contains_auth(ctx, request_kwargs, parameters, expected):
    request = requests.Request("GET", **request_kwargs).prepare()
    assert _contains_auth(ctx, Mock(_has_explicit_auth=False), request, parameters) == expected


@pytest.mark.parametrize(
    ("key", "parameters"),
    [
        ("headers", [{"name": "A", "in": "header"}]),
        ("query", [{"name": "A", "in": "query"}]),
        ("cookies", [{"name": "A", "in": "cookie"}]),
    ],
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


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("basic")
def test_explicit_auth_cli(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "-c", "ignored_auth", "--auth=test:test", "--hypothesis-max-examples=1") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.parametrize("with_error", [True, False])
@pytest.mark.snapshot(replace_statistic=True)
def test_stateful_in_cli_no_error(ctx, cli, with_error, base_url, snapshot_cli):
    target = "ignored" if with_error else "valid"
    schema_path = ctx.openapi.write_schema(
        {
            "/basic": {
                "get": {
                    "operationId": "valid",
                    "security": [{"basicAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/ignored_auth": {
                "get": {
                    "operationId": "ignored",
                    "security": [{"heisenAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/users/": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "first_name": {"type": "string", "minLength": 3},
                                        "last_name": {"type": "string", "minLength": 3},
                                    },
                                    "required": ["first_name", "last_name"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {
                            "description": "OK",
                            "links": {
                                "TestLink": {"operationId": target, "parameters": {}},
                            },
                        }
                    },
                }
            },
        },
        components={
            "securitySchemes": {
                "basicAuth": {"scheme": "basic", "type": "http"},
                "heisenAuth": {"scheme": "basic", "type": "http"},
            },
        },
    )
    assert (
        cli.run(
            str(schema_path),
            f"--base-url={base_url}",
            "-c",
            "ignored_auth",
            "--header=Authorization: Basic dGVzdDp0ZXN0",
            "--hypothesis-max-examples=100",
            "--experimental=stateful-only",
            "--experimental=stateful-test-runner",
            "--show-trace",
        )
        == snapshot_cli
    )


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


def make_app(auth_location):
    if auth_location == "query":
        cls = "APIKeyQuery"
    elif auth_location == "cookie":
        cls = "APIKeyCookie"
    elif auth_location == "header":
        cls = "APIKeyHeader"
    else:
        raise ValueError(f"Unknown auth location: {auth_location}")
    return f"""
from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.security import {cls}


app = FastAPI()

API_KEY = "42"
API_KEY_NAME = "api_key"

api_key = {cls}(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key: str = Security(api_key)):
    if api_key == API_KEY:
        return api_key
    raise HTTPException(status_code=403, detail="Could not validate credentials")

@app.get("/data")
async def data(api_key: str = Depends(get_api_key)):
    return {{"message": "Authenticated"}}
        """


@pytest.mark.parametrize("location", ["query", "cookie"])
def test_auth_via_override_cli(cli, testdir, snapshot_cli, location):
    # When auth is provided via `--set-*`
    module = testdir.make_importable_pyfile(make_app(location))
    # Then it should counts during auth detection
    assert (
        cli.run(
            "/openapi.json",
            f"--app={module.purebasename}:app",
            "-c",
            "ignored_auth",
            "--experimental=openapi-3.1",
            f"--set-{location}=api_key=42",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("location", ["query", "cookie", "header"])
def test_auth_via_setitem(testdir, location):
    app = make_app(location)
    if location == "query":
        container = "query"
    elif location == "cookie":
        container = "cookies"
    elif location == "header":
        container = "headers"
    else:
        raise ValueError(f"Unknown auth location: {location}")
    testdir.makepyfile(
        f"""
{app}
from hypothesis import settings
import schemathesis
schemathesis.experimental.OPEN_API_3_1.enable()

schema = schemathesis.from_asgi("/openapi.json", app)

@schema.parametrize()
@settings(max_examples=3)
def test_update(case):
    case.{container}["api_key"] = "42"
    case.call_and_validate()

@schema.parametrize()
@settings(max_examples=3)
def test_replace(case):
    case.{container} = None
    case.{container} = {{"api_key": "42"}}
    case.call_and_validate()


@schema.parametrize()
@settings(max_examples=3)
@schema.override({container}={{"api_key": "42"}})
def test_override(case):
    case.call_and_validate()
"""
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=3)
