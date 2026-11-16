from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from flask import jsonify, request
from hypothesis import given, settings
from requests.auth import HTTPBasicAuth

import schemathesis
from schemathesis.auths import AuthContext, AuthStorage, CachingAuthProvider
from schemathesis.core.errors import AuthenticationError, IncorrectUsage
from schemathesis.engine import Status, from_schema
from schemathesis.engine.events import ScenarioFinished
from schemathesis.engine.run import PhaseName
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.specs.openapi.auths import DynamicTokenAuthProvider, HttpBearerAuthProvider
from schemathesis.specs.openapi.checks import negative_data_rejection
from test.utils import EventStream

TOKEN = "EXAMPLE-TOKEN"
CERT_PATH = Path(__file__).parent.parent / "cli" / "cert.pem"


@pytest.fixture
def token():
    return TOKEN


@pytest.fixture
def auth_storage():
    return AuthStorage()


@pytest.fixture
def auth_provider_class(token):
    class Auth:
        def __init__(self):
            self.get_calls = 0
            self.set_calls = 0

        def get(self, case, context):
            self.get_calls += 1
            return token

        def set(self, case, data, context):
            self.set_calls += 1
            case.headers = {"Authorization": f"Bearer {data}"}

    return Auth


def test_cache(auth_provider_class, token, case_factory):
    current_time = 0.0

    def timer():
        return current_time

    context = AuthContext(operation=case_factory().operation, app=None)
    provider = CachingAuthProvider(auth_provider_class(), timer=timer)
    assert provider.get(None, context) == token
    assert provider.get(None, context) == token
    assert provider.provider.get_calls == 1
    current_time += provider.refresh_interval
    assert provider.get(None, context) == token
    assert provider.provider.get_calls == 2
    assert provider.get(None, context) == token
    assert provider.provider.get_calls == 2


def test_register_invalid(auth_storage):
    with pytest.raises(TypeError, match="`Invalid` does not implement the `AuthProvider` protocol"):

        @auth_storage.auth()
        class Invalid: ...


def test_apply_twice(ctx, auth_provider_class):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    with pytest.raises(IncorrectUsage, match="`test` has already been decorated with `apply`"):

        @schema.auth(auth_provider_class)
        @schema.auth(auth_provider_class)
        def test(case):
            pass


def test_register_valid(auth_storage, auth_provider_class):
    auth_storage.auth(refresh_interval=None)(auth_provider_class)
    assert auth_storage.providers
    assert isinstance(auth_storage.providers[0], auth_provider_class)


def test_register_cached(auth_storage, auth_provider_class):
    # The default `refresh_interval` wraps the provider in a cache.
    auth_storage.auth()(auth_provider_class)
    assert auth_storage.providers
    assert isinstance(auth_storage.providers[0], CachingAuthProvider)
    assert isinstance(auth_storage.providers[0].provider, auth_provider_class)


def test_set_noop(auth_storage, case_factory):
    case = case_factory()
    with pytest.raises(IncorrectUsage, match="No auth provider is defined."):
        auth_storage.set(case, AuthContext(operation=case.operation, app=None))
    # Earlier checks normally prevent this.


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"refresh_interval": None}, "Boom"),
        ({}, "Boom"),
        ({"cache_by_key": lambda case, context: 1 / 0}, "division by zero"),
    ],
    ids=["uncached", "cached", "broken-cache-key"],
)
def test_filtered_provider_error_names_user_class(ctx, kwargs, message):
    schema = ctx.openapi.load_schema({"/a": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @schema.auth(**kwargs).apply_to(method="GET")
    class Broken:
        def get(self, case, context):
            raise ValueError("Boom")

        def set(self, case, data, context):
            pass

    @given(schema["/a"]["GET"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        pass

    with pytest.raises(AuthenticationError, match=rf"^Error in 'Broken.get\(\)': {message}\n"):
        test()


MULTI_SCOPE_SCHEMA = {
    "openapi": "3.0.3",
    "info": {
        "title": "Cats Schema",
        "description": "Cats Schema",
        "version": "1.0.0",
        "contact": {"email": "info@cats.cat"},
    },
    "servers": [{"url": "https://cats.cat"}],
    "tags": [{"name": "cat"}],
    "paths": {
        "/v1/cats": {
            "get": {
                "description": "List cats",
                "operationId": "listCats",
                "tags": ["cat"],
                "responses": {
                    "200": {
                        "description": "List of cats",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                            }
                        },
                    }
                },
                "security": [{"oAuthBearer": ["list"]}],
            },
            "post": {
                "description": "Create a cat",
                "operationId": "createCat",
                "tags": ["cat"],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Cat"}}},
                },
                "responses": {
                    "200": {
                        "description": "Newly created cat",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Cat"}}},
                    }
                },
                "security": [{"oAuthBearer": ["create"]}],
            },
        }
    },
    "components": {
        "schemas": {
            "Cat": {
                "required": ["name"],
                "type": "object",
                "properties": {"name": {"type": "string", "example": "Macavity"}},
                "additionalProperties": False,
            }
        },
        "securitySchemes": {
            "oAuthBearer": {
                "type": "oauth2",
                "flows": {
                    "clientCredentials": {
                        "tokenUrl": "/oauth/token",
                        "scopes": {"list": "List cats", "create": "Create cats"},
                    }
                },
            }
        },
    },
}


def test_auth_cache_with_scopes(ctx):
    # See GH-1775
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        MULTI_SCOPE_SCHEMA["paths"],
        version=MULTI_SCOPE_SCHEMA["openapi"],
        components=MULTI_SCOPE_SCHEMA["components"],
        servers=MULTI_SCOPE_SCHEMA["servers"],
        tags=MULTI_SCOPE_SCHEMA["tags"],
    )
    schema.config.update(base_url=f"{api.base_url}/api")

    counts = {}

    def get_scopes(context):
        security = context.operation.definition.raw.get("security", [])
        if not security:
            return None

        requirements = context.operation.definition.raw.get("security", schema.raw_schema.get("security", []))
        scopes = security[0][next(key for requirement in requirements for key in requirement)]
        if not scopes:
            return None
        return frozenset(scopes)

    def cache_by_key(case: Case, context: AuthContext) -> str:
        scopes = get_scopes(context) or []
        return ",".join(scopes)

    @schema.auth(cache_by_key=cache_by_key)
    class OAuth2Bearer:
        def get(self, case, context: AuthContext) -> str | None:
            if not (scopes := get_scopes(context)):
                return None
            key = ",".join(sorted(scopes))
            counts.setdefault(key, 0)
            counts[key] += 1
            return f"Token -> {key}"

        def set(self, case: Case, data: str, context: AuthContext) -> None:
            case.headers = case.headers or {}
            if not data:
                return
            case.headers["Authorization"] = f"Bearer {data}"

    list(from_schema(schema).execute())
    assert counts["list"] == 1
    assert counts["create"] == 1


def test_dynamic_auth_malformed_payload_content_type(auth_operation):
    provider = DynamicTokenAuthProvider(
        path="/api/body-auth",
        method="post",
        payload={"key": "value"},
        payload_content_type="json",
        extract_from="body",
        extract_selector="/access_token",
        _applier=HttpBearerAuthProvider(bearer=""),
    )
    with pytest.raises(
        AuthenticationError, match="Malformed payload_content_type 'json': Malformed media type: `json`"
    ):
        provider.get(auth_operation.Case(), AuthContext(operation=auth_operation, app=None))


def test_dynamic_auth_token_fetch_with_request_cert(auth_operation):
    auth_operation.schema.config.request_cert = str(CERT_PATH)
    provider = DynamicTokenAuthProvider(
        path="/api/body-auth",
        method="post",
        payload=None,
        extract_from="body",
        extract_selector="/access_token",
        _applier=HttpBearerAuthProvider(bearer=""),
    )
    assert provider.get(auth_operation.Case(), AuthContext(operation=auth_operation, app=None)) == "test-token"


@pytest.mark.parametrize(
    "registrar",
    [lambda schema: schema.auth, lambda schema: schemathesis.auth],
    ids=["schema-auth", "global-auth"],
)
def test_negative_data_rejection_no_false_positive_with_cookie_security_scheme(ctx, app_runner, registrar):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/secure": {
                "get": {
                    "responses": {
                        "200": {"description": "OK"},
                        "401": {"description": "Unauthorized"},
                    },
                    "security": [{"session_cookie": []}],
                }
            }
        },
        components={"securitySchemes": {"session_cookie": {"type": "apiKey", "in": "cookie", "name": "session"}}},
    )

    @app.route("/api/secure")
    def secure():
        if not request.cookies.get("session"):
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({}), 200

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))

    @registrar(schema)(refresh_interval=None)
    class CookieAuth:
        def get(self, case, context):
            return "token123"

        def set(self, case, data, context):
            case.headers = case.headers or {}
            case.headers["Cookie"] = f"session={data}"

    stream = EventStream(
        schema,
        phases=[PhaseName.COVERAGE],
        checks=[negative_data_rejection],
    ).execute()

    assert stream.find(ScenarioFinished, status=Status.FAILURE) is None


def _wsgi_echo_authorization(ctx):
    app, _ = ctx.openapi.make_flask_app({"/echo": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/echo")
    def echo():
        return jsonify({"authorization": request.headers.get("Authorization")})

    return schemathesis.openapi.from_wsgi("/openapi.json", app)


def _asgi_echo_authorization(ctx):
    app = FastAPI()

    @app.get("/echo")
    async def echo(request: Request):
        return {"authorization": request.headers.get("Authorization")}

    return schemathesis.openapi.from_asgi("/openapi.json", app)


@pytest.mark.parametrize("load_schema", [_wsgi_echo_authorization, _asgi_echo_authorization], ids=["wsgi", "asgi"])
def test_set_from_requests_reaches_in_process_app(ctx, load_schema):
    schema = load_schema(ctx)
    schema.auth.set_from_requests(HTTPBasicAuth("user", "pass"))
    (case,) = generate_many(schema["/echo"]["GET"].as_strategy(), 1)
    assert case.call().json() == {"authorization": "Basic dXNlcjpwYXNz"}


def _register_on_schema(schema, provider_class, refresh_interval):
    schema.auth(refresh_interval=refresh_interval)(provider_class)


def _register_globally(schema, provider_class, refresh_interval):
    schemathesis.auth(refresh_interval=refresh_interval)(provider_class)


def _register_for_strategy(schema, provider_class, refresh_interval):
    auth_storage = AuthStorage()
    auth_storage.auth(refresh_interval=refresh_interval)(provider_class)
    return auth_storage


# A zero `refresh_interval` expires the cached token before the next use.
@pytest.mark.parametrize(
    "register",
    [_register_on_schema, _register_globally, _register_for_strategy],
    ids=["schema-auth", "global-auth", "strategy-auth"],
)
@pytest.mark.parametrize(("refresh_interval", "refetches"), [(0, 1), (300, 0)], ids=["expired", "cached"])
def test_reapply_auth_refetches_only_expired_tokens(ctx, register, refresh_interval, refetches):
    schema = _wsgi_echo_authorization(ctx)
    tokens = []

    class Counting:
        def get(self, case, context):
            tokens.append(f"token-{len(tokens) + 1}")
            return tokens[-1]

        def set(self, case, data, context):
            case.headers["Authorization"] = f"Bearer {data}"

    auth_storage = register(schema, Counting, refresh_interval)
    (case,) = generate_many(schema["/echo"]["GET"].as_strategy(auth_storage=auth_storage), 1)
    generated = len(tokens)
    case.reapply_auth()
    assert (case.call().json(), len(tokens) - generated) == ({"authorization": f"Bearer {tokens[-1]}"}, refetches)


def test_reapply_auth_keeps_credentials_it_did_not_set(ctx):
    app, _ = ctx.openapi.make_flask_app(
        {"/echo": {"get": {"security": [{"ApiKey": []}], "responses": {"200": {"description": "OK"}}}}},
        components={"securitySchemes": {"ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}},
    )

    @app.route("/echo")
    def echo():
        return jsonify({"key": request.headers.get("X-API-Key")})

    schema = schemathesis.openapi.from_wsgi("/openapi.json", app)

    @schema.auth(refresh_interval=0)
    class Configured:
        def get(self, case, context):
            return "configured"

        def set(self, case, data, context):
            case.headers["X-API-Key"] = data

    operation = schema["/echo"]["GET"]
    by_hand = operation.Case(headers={"X-API-Key": "by-hand"})
    (generated,) = generate_many(operation.as_strategy(), 1)
    (negated,) = generate_many(operation.as_strategy(generation_mode=schemathesis.GenerationMode.NEGATIVE), 1)
    for case in (by_hand, generated, negated):
        case.reapply_auth()
    assert [
        by_hand.call().json(),
        generated.call(headers={"X-API-Key": "call-time"}).json(),
        negated.call().json(),
    ] == [{"key": "by-hand"}, {"key": "call-time"}, {"key": None}]
