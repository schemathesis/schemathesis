from __future__ import annotations

import threading

import pytest

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
