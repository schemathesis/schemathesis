from __future__ import annotations

import threading

import pytest
from flask import make_response, request

import schemathesis
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
