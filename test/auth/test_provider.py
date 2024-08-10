from __future__ import annotations

import warnings

import pytest

import schemathesis
from schemathesis.auths import AuthContext, AuthStorage, CachingAuthProvider
from schemathesis.exceptions import UsageError
from schemathesis.models import Case
from schemathesis.runner import from_schema

TOKEN = "EXAMPLE-TOKEN"


@pytest.fixture
def token():
    return TOKEN


@pytest.fixture
def auth_storage():
    return AuthStorage()


@pytest.fixture(params=["old", "new"])
def calling_convention(request):
    return request.param


@pytest.fixture
def auth_provider_class(calling_convention, token):
    class Auth:
        def __init__(self):
            self.get_calls = 0
            self.set_calls = 0

        if calling_convention == "old":

            def get(self, context):
                self.get_calls += 1
                return token

        else:

            def get(self, case, context):
                self.get_calls += 1
                return token

        def set(self, case, data, context):
            self.set_calls += 1
            case.headers = {"Authorization": f"Bearer {data}"}

    return Auth


def test_cache(calling_convention, auth_provider_class, token, mocker):
    current_time = 0.0

    def timer():
        return current_time

    context = mocker.create_autospec(AuthContext)
    # When caching provider is used
    provider = CachingAuthProvider(auth_provider_class(), timer=timer)
    with warnings.catch_warnings(record=True) as logs:
        # Then all `get` calls are cached
        assert provider.get(None, context) == token
        assert provider.get(None, context) == token
        assert provider.provider.get_calls == 1
        # And refresh happens when the refresh period has passed
        current_time += provider.refresh_interval
        assert provider.get(None, context) == token
        assert provider.provider.get_calls == 2
        assert provider.get(None, context) == token
        assert provider.provider.get_calls == 2  # No increase

    if calling_convention == "old":
        assert len(logs) == 2
        expected = (
            "The method 'get' of your AuthProvider is using the old calling convention, which is deprecated and "
            "will be removed in Schemathesis 4.0. Please update it to accept both 'case' and 'context' as arguments."
        )
        assert str(logs[0].message) == str(logs[1].message) == expected


def test_register_invalid(auth_storage):
    # When the class implementation is wrong
    # Then it should not be possible to register it

    with pytest.raises(TypeError, match="`Invalid` is not a valid auth provider"):

        @auth_storage.register()
        class Invalid:
            pass


def test_apply_twice(openapi3_schema, auth_provider_class):
    # When auth is registered twice
    # Then it is an error
    with pytest.raises(UsageError, match="`test` has already been decorated with `apply`"):

        @openapi3_schema.auth(auth_provider_class)
        @openapi3_schema.auth(auth_provider_class)
        def test(case):
            pass


def test_register_valid(auth_storage, auth_provider_class):
    # When the class implementation is valid
    # Then it should be possible to register it without issues
    auth_storage.register(refresh_interval=None)(auth_provider_class)
    assert auth_storage.providers
    assert isinstance(auth_storage.providers[0], auth_provider_class)


def test_register_cached(auth_storage, auth_provider_class):
    # When the `refresh_interval` is not None
    auth_storage.register()(auth_provider_class)
    # Then the actual provider should be cached
    assert auth_storage.providers
    assert isinstance(auth_storage.providers[0], CachingAuthProvider)
    assert isinstance(auth_storage.providers[0].provider, auth_provider_class)


def test_set_noop(auth_storage, mocker):
    # When `AuthStorage.set` is called without `provider`
    with pytest.raises(UsageError, match="No auth provider is defined."):
        auth_storage.set(mocker.create_autospec(Case), mocker.create_autospec(AuthContext))
    # This normally should not happen, as it is checked before.


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


def test_auth_cache_with_scopes():
    # See GH-1775
    schema = schemathesis.from_dict(MULTI_SCOPE_SCHEMA)

    counts = {}

    def get_scopes(context):
        security = context.operation.definition.raw.get("security", [])
        if not security:
            return None
        scopes = security[0][context.operation.get_security_requirements()[0]]
        if not scopes:
            return None
        return frozenset(scopes)

    def cache_by_key(case: Case, context: AuthContext) -> str:
        scopes = get_scopes(context) or []
        return ",".join(scopes)

    @schema.auth(cache_by_key=cache_by_key)
    class OAuth2Bearer:
        def get(self, context: AuthContext) -> str | None:
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

    _, *ev_ = from_schema(schema, dry_run=True).execute()
    assert counts["list"] == 1
    assert counts["create"] == 1
