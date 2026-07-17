import pytest

import schemathesis
from schemathesis.auths import (
    GLOBAL_AUTH_STORAGE,
    TOKEN_FETCH_BREAKER_THRESHOLD,
    AuthContext,
    CachingAuthProvider,
    KeyedCachingAuthProvider,
)
from schemathesis.core.errors import AuthenticationError
from schemathesis.specs.openapi.auths import DynamicTokenAuthProvider, HttpBearerAuthProvider


def _dynamic_provider(inner_cls):
    return CachingAuthProvider(
        inner_cls(
            path="/api/fail",
            method="post",
            payload=None,
            extract_from="body",
            extract_selector="/access_token",
            _applier=HttpBearerAuthProvider(bearer=""),
        ),
        refresh_interval=10_000,
    )


def test_repeated_fetch_failures_stop_hammering_login(auth_operation):
    attempts = {"n": 0}

    class AlwaysLocked(DynamicTokenAuthProvider):
        def get(self, case, ctx):
            attempts["n"] += 1
            raise AuthenticationError("AlwaysLocked", "get", "account locked")

    provider = _dynamic_provider(AlwaysLocked)
    ctx = AuthContext(operation=auth_operation, app=None)
    case = auth_operation.Case()
    for _ in range(50):
        with pytest.raises(AuthenticationError):
            provider.get(case, ctx)
    assert attempts["n"] == TOKEN_FETCH_BREAKER_THRESHOLD


def test_successful_fetch_resets_failure_counter(auth_operation):
    attempts = {"n": 0}
    schedule = [False, False, True, False, False, False]

    class Flaky(DynamicTokenAuthProvider):
        def get(self, case, ctx):
            ok = schedule[attempts["n"]]
            attempts["n"] += 1
            if ok:
                return "token"
            raise AuthenticationError("Flaky", "get", "transient blip")

    provider = _dynamic_provider(Flaky)
    ctx = AuthContext(operation=auth_operation, app=None)
    case = auth_operation.Case()
    for ok in schedule:
        provider.invalidate()
        if ok:
            assert provider.get(case, ctx) == "token"
        else:
            with pytest.raises(AuthenticationError):
                provider.get(case, ctx)
    assert attempts["n"] == len(schedule)


def test_keyed_breaker_is_per_key(auth_operation):
    attempts = {"admin": 0, "user": 0}
    scope = {"value": "admin"}

    class PerScope(DynamicTokenAuthProvider):
        def get(self, case, ctx):
            current = scope["value"]
            attempts[current] += 1
            if current == "admin":
                raise AuthenticationError("PerScope", "get", "admin locked")
            return "user-token"

    provider = KeyedCachingAuthProvider(
        PerScope(
            path="/api/fail",
            method="post",
            payload=None,
            extract_from="body",
            extract_selector="/access_token",
            _applier=HttpBearerAuthProvider(bearer=""),
        ),
        refresh_interval=10_000,
        cache_by_key=lambda case, ctx: scope["value"],
    )
    ctx = AuthContext(operation=auth_operation, app=None)
    case = auth_operation.Case()
    for _ in range(TOKEN_FETCH_BREAKER_THRESHOLD + 2):
        with pytest.raises(AuthenticationError):
            provider.get(case, ctx)

    scope["value"] = "user"
    assert provider.get(case, ctx) == "user-token"
    assert attempts["user"] == 1


def test_invalidate_forces_refetch(auth_operation):
    calls = {"n": 0}

    class Counting(DynamicTokenAuthProvider):
        def get(self, case, ctx):
            calls["n"] += 1
            return "t"

    provider = CachingAuthProvider(
        Counting(
            path="/api/body-auth",
            method="post",
            payload=None,
            extract_from="body",
            extract_selector="/access_token",
            _applier=HttpBearerAuthProvider(bearer=""),
        ),
        refresh_interval=10_000,
    )
    ctx = AuthContext(operation=auth_operation, app=None)
    case = auth_operation.Case()
    provider.get(case, ctx)
    provider.get(case, ctx)
    assert calls["n"] == 1
    provider.invalidate()
    provider.get(case, ctx)
    assert calls["n"] == 2


@pytest.mark.parametrize(
    "kwargs, expected",
    [({}, [401]), ({"retry_on": [401, 440]}, [401, 440]), ({"retry_on": []}, [])],
    ids=["default", "explicit", "empty-disables"],
)
def test_auth_decorator_retry_on(kwargs, expected):

    @schemathesis.auth(**kwargs)
    class TokenAuth:
        def get(self, case, ctx):
            return "t"

        def set(self, case, data, ctx):
            case.headers["Authorization"] = f"Bearer {data}"

    assert GLOBAL_AUTH_STORAGE.providers[-1].retry_on == expected
