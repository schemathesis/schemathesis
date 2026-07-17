from __future__ import annotations

import threading

import pytest
from flask import make_response, request

import schemathesis
from schemathesis.auths import REAUTH_BREAKER_THRESHOLD, ReauthState
from schemathesis.config._auth import DynamicTokenAuthConfig
from schemathesis.core.result import Ok
from schemathesis.engine.context import EngineContext
from schemathesis.engine.health import TIGHTENED_TIMEOUT_SECONDS


def _build_schema_and_operation(ctx):
    schema = ctx.openapi.load_schema({"/a": {"get": {"responses": {"200": {"description": "OK"}}}}})
    operation = next(result.ok() for result in schema.get_all_operations() if isinstance(result, Ok))
    return schema, operation


def test_get_transport_kwargs_no_override_when_operation_healthy(ctx):
    schema, operation = _build_schema_and_operation(ctx)
    schema.config.update(request_timeout=5.0)
    engine = EngineContext(schema=schema, stop_event=threading.Event())
    assert engine.get_transport_kwargs(operation=operation)["timeout"] == 5.0


@pytest.mark.parametrize(
    ("user_timeout",),
    [(None,), (5.0,)],
    ids=["user-timeout-unset", "user-timeout-larger-than-override"],
)
def test_get_transport_kwargs_applies_override(ctx, user_timeout):
    schema, operation = _build_schema_and_operation(ctx)
    if user_timeout is not None:
        schema.config.update(request_timeout=user_timeout)
    engine = EngineContext(schema=schema, stop_event=threading.Event())
    engine.health.record_transport_failure(operation_label=operation.label, now=10.0)
    engine.health.record_transport_failure(operation_label=operation.label, now=11.0)
    assert engine.get_transport_kwargs(operation=operation)["timeout"] == TIGHTENED_TIMEOUT_SECONDS


def test_get_transport_kwargs_does_not_override_when_user_timeout_already_smaller(ctx):
    schema, operation = _build_schema_and_operation(ctx)
    user_timeout = TIGHTENED_TIMEOUT_SECONDS / 2  # tighter than the health override
    schema.config.update(request_timeout=user_timeout)
    engine = EngineContext(schema=schema, stop_event=threading.Event())
    engine.health.record_transport_failure(operation_label=operation.label, now=10.0)
    engine.health.record_transport_failure(operation_label=operation.label, now=11.0)
    assert engine.get_transport_kwargs(operation=operation)["timeout"] == user_timeout


def test_get_transport_kwargs_skips_override_for_no_operation(ctx):
    schema, operation = _build_schema_and_operation(ctx)
    schema.config.update(request_timeout=5.0)
    engine = EngineContext(schema=schema, stop_event=threading.Event())
    # Even with failures recorded, override doesn't apply when no operation is passed.
    engine.health.record_transport_failure(operation_label=operation.label, now=10.0)
    engine.health.record_transport_failure(operation_label=operation.label, now=11.0)
    assert engine.get_transport_kwargs()["timeout"] == 5.0


def test_get_transport_kwargs_override_reverts_after_completion(ctx):
    schema, operation = _build_schema_and_operation(ctx)
    schema.config.update(request_timeout=5.0)
    engine = EngineContext(schema=schema, stop_event=threading.Event())
    engine.health.record_transport_failure(operation_label=operation.label, now=10.0)
    engine.health.record_transport_failure(operation_label=operation.label, now=11.0)
    assert engine.get_transport_kwargs(operation=operation)["timeout"] == TIGHTENED_TIMEOUT_SECONDS
    engine.health.record_completion(operation_label=operation.label)
    assert engine.get_transport_kwargs(operation=operation)["timeout"] == 5.0


def test_managed_session_does_not_leak_response_cookies(ctx, app_runner):
    received_cookies = []

    app, _ = ctx.openapi.make_flask_app({"/cookie": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/cookie")
    def cookie():
        received_cookies.append(request.cookies.get("session"))
        response = make_response("")
        response.set_cookie("session", "leaked")
        return response

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    operation = next(result.ok() for result in schema.get_all_operations() if isinstance(result, Ok))
    engine = EngineContext(schema=schema, stop_event=threading.Event())
    kwargs = engine.get_transport_kwargs(operation=operation)

    operation.Case().call(**kwargs)
    operation.Case().call(**kwargs)

    # The cookie set by the first response must not be replayed on the second request.
    assert received_cookies == [None, None]


def _simple_schema(ctx):
    return ctx.openapi.load_schema({"/a": {"get": {"responses": {"200": {"description": "OK"}}}}})


def _reauth_state():
    return ReauthState(retry_on_statuses=frozenset({401}))


def test_reauth_breaker_trips_exactly_at_threshold():
    state = _reauth_state()

    for _ in range(REAUTH_BREAKER_THRESHOLD - 1):
        state.note_replay(401)
        assert state.disabled is False
        assert state.broke is False

    state.note_replay(401)

    assert state.disabled is True
    assert state.broke is True


def test_reauth_recovery_resets_consecutive_counter_and_increments_reauth_count():
    state = _reauth_state()

    state.note_replay(401)
    state.note_replay(200)

    assert state.reauth_count == 1
    assert state.disabled is False

    # The consecutive-failure counter was reset by the recovery, so it takes a fresh
    # run of `REAUTH_BREAKER_THRESHOLD` failures to trip the breaker.
    for _ in range(REAUTH_BREAKER_THRESHOLD - 1):
        state.note_replay(401)
        assert state.disabled is False

    state.note_replay(401)
    assert state.disabled is True


def test_reauth_disabled_predicate_reflects_breaker_state():
    state = _reauth_state()

    assert state.disabled is False

    for _ in range(REAUTH_BREAKER_THRESHOLD):
        state.note_replay(401)

    assert state.disabled is True


def test_server_error_replay_not_counted_as_recovery():
    # A 5xx replay is neither a recovery nor a reauth failure: no reauth_count, and the failure streak is preserved.
    state = _reauth_state()

    state.note_replay(401)
    state.note_replay(500)

    assert state.reauth_count == 0
    assert state.disabled is False
    assert state._consecutive_failures == 1


def test_transient_non_retry_status_does_not_reset_breaker():
    state = _reauth_state()

    state.note_replay(401)
    state.note_replay(401)
    state.note_replay(500)
    state.note_replay(401)

    assert state.disabled is True


def test_reauth_count_only_counts_successful_recovery():
    state = _reauth_state()

    state.note_replay(403)
    state.note_replay(200)

    assert state.reauth_count == 1


def test_retry_on_statuses_empty_by_default(ctx):
    schema = _simple_schema(ctx)
    engine = EngineContext(schema=schema, stop_event=threading.Event())

    assert engine.reauth.retry_on_statuses == frozenset()


def test_retry_on_statuses_unions_dynamic_scheme_and_registered_auth_provider(ctx):
    schema = _simple_schema(ctx)
    schema.config.auth.dynamic.schemes["petstore_auth"] = DynamicTokenAuthConfig(path="/token", retry_on=[401, 419])

    @schemathesis.auth(retry_on=[503])
    class TokenAuth:
        def get(self, case, context):
            return "token"

        def set(self, case, data, context):
            case.headers = case.headers or {}
            case.headers["Authorization"] = f"Bearer {data}"

    engine = EngineContext(schema=schema, stop_event=threading.Event())
    assert engine.reauth.retry_on_statuses == frozenset({401, 419, 503})
