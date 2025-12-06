import json
from typing import Annotated
from unittest.mock import Mock

import pytest
import requests
from fastapi import FastAPI, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials
from hypothesis import Phase, given, settings
from starlette_testclient import TestClient

import schemathesis
from schemathesis.checks import CheckContext
from schemathesis.config import ChecksConfig
from schemathesis.core.failures import FailureGroup
from schemathesis.core.transport import Response
from schemathesis.engine import Status
from schemathesis.engine.events import ScenarioFinished
from schemathesis.engine.phases import PhaseName
from schemathesis.specs.openapi.checks import AuthKind, IgnoredAuth, _contains_auth, ignored_auth, remove_auth
from schemathesis.transport.requests import RequestsTransport
from test.utils import EventStream


def run(schema_url, **config):
    schema = schemathesis.openapi.from_url(schema_url)
    stream = EventStream(
        schema,
        phases=[PhaseName.FUZZING],
        checks=[ignored_auth],
        **config,
    ).execute()
    return stream.find(ScenarioFinished)


@pytest.mark.parametrize("with_generated", [True, False])
@pytest.mark.operations("ignored_auth")
def test_auth_is_not_checked(with_generated, schema_url):
    kwargs = {}
    if not with_generated:
        kwargs["with_security_parameters"] = False
    # When auth is present
    # And endpoint declares auth as a requirement but doesn't actually require it
    event = run(schema_url, **kwargs)
    # Then it is a failure
    recorder = event.recorder
    case = list(recorder.cases.values())[-1].value
    check = recorder.checks[case.id][-1]
    assert check.status == Status.FAILURE
    assert check.name == "ignored_auth"
    headers = recorder.cases[case.id].value.headers or {}
    response = recorder.interactions[case.id].response
    if with_generated:
        assert "Authorization" in headers
        assert response.json() == {"has_auth": True}
    else:
        assert "Authorization" not in headers
        assert response.json() == {"has_auth": False}


@pytest.mark.operations("basic")
def test_auth_is_checked(schema_url):
    # When auth is present (generated)
    # And endpoint declares auth as a requirement and checks it
    event = run(schema_url, headers={"Authorization": "Basic dGVzdDp0ZXN0"})
    # Then there is no failure
    assert event.status == Status.SUCCESS


@pytest.mark.operations("success")
def test_no_failure(schema_url):
    # When there is no auth
    event = run(schema_url)
    # Then there is no failure
    assert event.status == Status.SUCCESS


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("ignored_auth")
def test_keep_tls_verification(schema_url, mocker):
    # See GH-2613
    # `verify` and other options should not be ignored in `ignored_auth`
    send = mocker.spy(RequestsTransport, "send")
    run(
        schema_url,
        request_timeout=5,
        tls_verify=False,
        headers={"Authorization": "Basic dGVzdDp0ZXN0"},
    )
    for call in send.mock_calls:
        assert call.kwargs["timeout"] == 5
        assert not call.kwargs["verify"]
    send.reset_mock()

    schema = schemathesis.openapi.from_url(schema_url)

    operation = schema["/ignored_auth"]["get"]

    @given(operation.as_strategy())
    def test(case):
        try:
            case.call_and_validate(verify=False, timeout=5)
        except FailureGroup:
            pass

    test()

    for call in send.mock_calls:
        assert call.kwargs["timeout"] == 5
        assert not call.kwargs["verify"]


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("ignored_auth")
def test_file_loaded_schema_requires_explicit_base_url(openapi3_schema, openapi3_base_url, tmp_path):
    # See GH-3318
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(openapi3_schema.raw_schema))
    schema = schemathesis.openapi.from_path(schema_path)
    case = schema["/ignored_auth"]["get"].Case(headers={"Authorization": "Basic dGVzdDp0ZXN0"})
    with pytest.raises(FailureGroup) as exc_info:
        case.call_and_validate(base_url=openapi3_base_url, checks=[ignored_auth])
    assert any(isinstance(failure, IgnoredAuth) for failure in exc_info.value.exceptions)


@pytest.mark.parametrize(
    ("ctx", "request_kwargs", "parameters", "expected"),
    [
        (
            CheckContext(
                override=None,
                auth=None,
                headers=None,
                config=ChecksConfig(),
                transport_kwargs=None,
            ),
            {"url": "https://example.com", "headers": {"A": "V"}},
            [{"name": "A", "in": "header"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(
                override=None,
                auth=None,
                headers={"Foo": "Bar"},
                config=ChecksConfig(),
                transport_kwargs=None,
            ),
            {"url": "https://example.com", "headers": {"A": "V"}},
            [{"name": "A", "in": "header"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(
                override=None,
                auth=None,
                headers={"A": "V"},
                config=ChecksConfig(),
                transport_kwargs=None,
            ),
            {"url": "https://example.com", "headers": {"A": "V"}},
            [{"name": "A", "in": "header"}],
            AuthKind.EXPLICIT,
        ),
        (
            CheckContext(override=None, auth=None, headers={}, config=ChecksConfig(), transport_kwargs=None),
            {"url": "https://example.com", "headers": {"A": "V"}},
            [{"name": "B", "in": "header"}],
            None,
        ),
        (
            CheckContext(override=None, auth=None, headers={}, config=ChecksConfig(), transport_kwargs=None),
            {"url": "https://example.com?A=V"},
            [{"name": "A", "in": "query"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(override=None, auth=None, headers={}, config=ChecksConfig(), transport_kwargs=None),
            {"url": "https://example.com?A=V"},
            [{"name": "B", "in": "query"}],
            None,
        ),
        (
            CheckContext(override=None, auth=None, headers={}, config=ChecksConfig(), transport_kwargs=None),
            {"url": "https://example.com", "cookies": {"A": "V"}},
            [{"name": "A", "in": "cookie"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(
                override=None,
                auth=None,
                headers={"Cookie": "A=v;"},
                config=ChecksConfig(),
                transport_kwargs=None,
            ),
            {"url": "https://example.com", "cookies": {"A": "V"}},
            [{"name": "A", "in": "cookie"}],
            AuthKind.EXPLICIT,
        ),
        (
            CheckContext(
                override=None,
                auth=None,
                headers={"Cookie": "B=v;"},
                config=ChecksConfig(),
                transport_kwargs=None,
            ),
            {"url": "https://example.com", "cookies": {"A": "V"}},
            [{"name": "A", "in": "cookie"}],
            AuthKind.GENERATED,
        ),
        (
            CheckContext(override=None, auth=None, headers={}, config=ChecksConfig(), transport_kwargs=None),
            {"url": "https://example.com", "cookies": {"A": "V"}},
            [{"name": "B", "in": "cookie"}],
            None,
        ),
    ],
)
def test_contains_auth(ctx, request_kwargs, parameters, expected, response_factory):
    response = response_factory.requests()
    response.request = requests.Request("GET", **request_kwargs).prepare()
    assert (
        _contains_auth(ctx, Mock(_has_explicit_auth=False), Response.from_requests(response, verify=True), parameters)
        == expected
    )


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
    schema = schemathesis.openapi.from_url(schema_url)
    case = schema["/success"]["GET"].Case(**{key: {"A": "V"}})
    case = remove_auth(case, parameters)
    assert not getattr(case, key)


@pytest.mark.parametrize("ignores_auth", [True, False])
def test_proper_session(ignores_auth):
    app = FastAPI()

    @app.get("/", responses={200: {"model": {}}, 401: {"model": {}}, 403: {"model": {}}})
    def root(api_key: Annotated[str, Security(APIKeyHeader(name="x-api-key"))]):
        if not ignores_auth and api_key != "super-secret":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect key")

        return {"message": "Hello world"}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    @given(case=schema["/"]["GET"].as_strategy())
    @settings(max_examples=3, phases=[Phase.generate])
    def test(case):
        client = TestClient(app)
        case.call_and_validate(session=client)

    if ignores_auth:
        with pytest.raises(FailureGroup) as exc:
            test()
        assert str(exc.value.exceptions[0]).startswith("API accepts invalid authentication")
    else:
        test()


@pytest.mark.parametrize(
    ["ignores_auth", "expected"],
    [
        (True, "API accepts requests without authentication"),
        (False, "API accepts invalid authentication"),
    ],
)
def test_accepts_any_auth_if_explicit_is_present(ignores_auth, expected):
    app = FastAPI()

    @app.get("/", responses={200: {"model": {}}, 401: {"model": {}}, 403: {"model": {}}})
    async def root(
        credentials: HTTPAuthorizationCredentials | None = Security(APIKeyHeader(name="x-api-key", auto_error=False)),
    ):
        # Accept any auth, but raise an error if Authorization header is missing
        if not ignores_auth and credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization header is missing",
            )
        return {"message": "Hello world"}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    @given(case=schema["/"]["GET"].as_strategy())
    @settings(max_examples=3, phases=[Phase.generate])
    def test(case):
        client = TestClient(app)
        case.call_and_validate(session=client, headers={"x-api-key": "INCORRECT"})

    with pytest.raises(FailureGroup) as exc:
        test()
    assert str(exc.value.exceptions[0]).startswith(expected)


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("basic")
def test_explicit_auth_cli(cli, schema_url, snapshot_cli):
    assert (
        cli.run(schema_url, "-c", "ignored_auth", "--auth=test:test", "--max-examples=1", "--mode=positive")
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.parametrize("with_error", [True, False])
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
                                        "first_name": {"enum": ["John"]},
                                        "last_name": {"enum": ["Doe"]},
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
            f"--url={base_url}",
            "-c",
            "ignored_auth",
            "--header=Authorization: Basic dGVzdDp0ZXN0",
            "--max-examples=10",
            "--phases=stateful",
            "--mode=positive",
        )
        == snapshot_cli
    )


def test_custom_auth():
    app = FastAPI()
    token = "TEST"

    @app.get("/", responses={200: {"model": {}}, 401: {"model": {}}, 403: {"model": {}}})
    async def root(
        credentials: HTTPAuthorizationCredentials = Security(APIKeyHeader(name="x-api-key", auto_error=False)),
    ):
        if credentials != token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        return {"message": "Hello world"}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    @schema.auth()
    class Auth:
        def get(self, case, context):
            return token

        def set(self, case, data, context):
            case.headers = case.headers or {}
            case.headers["x-api-key"] = data

    @given(case=schema["/"]["GET"].as_strategy())
    @settings(max_examples=10, deadline=None)
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
    raise HTTPException(status_code=401, detail="Could not validate credentials")

@app.get("/data")
async def data(api_key: str = Depends(get_api_key)):
    return {{"message": "Authenticated"}}
        """


def test_explicit_auth_tuple_in_call_and_validate():
    # GH-3386: `auth` kwarg passed to `call_and_validate` is not forwarded to CheckContext
    app = FastAPI()
    api_key = APIKeyHeader(name="Authorization", auto_error=False)

    @app.get("/", responses={200: {"model": {}}, 401: {"model": {}}})
    def root(credentials: Annotated[str | None, Security(api_key)]):
        if credentials is None or credentials != "Basic dGVzdDp0ZXN0":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return {"message": "OK"}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)
    case = schema["/"]["GET"].Case()
    client = TestClient(app)
    # Valid auth passed as tuple - should NOT raise IgnoredAuth
    case.call_and_validate(session=client, auth=("test", "test"), checks=[ignored_auth])


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
from schemathesis import GenerationMode
from schemathesis.specs.openapi.checks import ignored_auth

schema = schemathesis.openapi.from_asgi("/openapi.json", app)
schema.config.generation.update(modes=[GenerationMode.POSITIVE])

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
def test_explicit(case):
    if "{container}" == "query":
        key = "params"
    else:
        key = "{container}"
    case.call_and_validate(checks=[ignored_auth], **{{key: {{"api_key": "42"}}}})

schema2 = schemathesis.openapi.from_asgi("/openapi.json", app)
schema2.config.generation.update(modes=[GenerationMode.POSITIVE])
schema2.config.update(parameters={{"api_key": "42"}})

@schema2.parametrize()
@settings(max_examples=3)
def test_override(case):
    case.call_and_validate()
""",
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=4)
