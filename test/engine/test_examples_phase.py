from __future__ import annotations

import re
from collections.abc import Callable

import hypothesis
import hypothesis.errors
import jsonschema_rs
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.errors import InvalidRegexPattern, InvalidRegexType, InvalidSchema, SerializationNotPossible
from schemathesis.core.jsonschema import make_validator_for
from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseName
from test.utils import EventStream


def _examples_only(schema) -> EventStream:
    return EventStream(schema, phases=[PhaseName.EXAMPLES]).execute()


def _example_schema(ctx):
    return ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "parameters": [
                        {
                            "name": "key",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "example": "value",
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
                                "example": {"x": "any"},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )


def _items_with_query_example(ctx, app_runner, *, status_code: int = 200):
    paths = {
        "/items": {
            "get": {
                "parameters": [
                    {
                        "name": "kind",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                        "example": "alpha",
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/items", methods=["GET"])
    def items():
        return ("", status_code)

    return app, schemathesis.openapi.from_url(app_runner.openapi_url(app))


def _hooked_examples_stream(ctx, raise_naturally: Callable[[], None]) -> EventStream:
    # `raise_naturally` must throw via real execution — never `raise SomeException(...)`.
    schema = _example_schema(ctx)

    @schema.hook
    def before_generate_query(context, strategy):
        raise_naturally()
        return strategy

    return _examples_only(schema)


def test_examples_phase_skip_when_no_examples_defined(ctx):
    schema = ctx.openapi.load_schema({"/items": {"get": {"responses": {"200": {"description": "OK"}}}}})

    stream = _examples_only(schema)

    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.EXAMPLES)
    assert finished
    assert all(event.status == Status.SKIP for event in finished), [event.status for event in finished]
    assert all(event.skip_reason == "No examples in schema" for event in finished)


def test_examples_phase_success_runs_through_iteration(ctx, app_runner):
    app, schema = _items_with_query_example(ctx, app_runner)
    stream = _examples_only(schema)

    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.EXAMPLES)
    assert finished
    assert any(event.status == Status.SUCCESS for event in finished), [event.status for event in finished]
    calls = [request for request in app.config["captured_requests"] if request.path == "/items"]
    assert any(request.query.get("kind") == "alpha" for request in calls), [r.query for r in calls]


def test_examples_phase_failure_status_for_check_failure(ctx, app_runner):
    _, schema = _items_with_query_example(ctx, app_runner, status_code=500)
    stream = _examples_only(schema)

    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.EXAMPLES)
    assert any(event.status == Status.FAILURE for event in finished), [event.status for event in finished]


def test_examples_phase_promotes_success_to_failure_on_continue_on_failure(ctx, app_runner):
    _, schema = _items_with_query_example(ctx, app_runner, status_code=500)
    schema.config.continue_on_failure = True

    stream = _examples_only(schema)

    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.EXAMPLES)
    assert any(event.status == Status.FAILURE for event in finished), [event.status for event in finished]


def test_examples_phase_translates_generation_exception(ctx):
    schema = _example_schema(ctx)

    @schema.hook
    def before_generate_query(context, strategy):
        raise RuntimeError("boom during generation")

    stream = _examples_only(schema)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any("boom during generation" in str(event.value) for event in errors), [str(e.value) for e in errors]
    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.EXAMPLES)
    assert any(event.status == Status.ERROR for event in finished), [event.status for event in finished]


def test_examples_phase_drains_deduplicated_errors(ctx):
    schema = _example_schema(ctx)

    with ctx.restore_hooks():

        @schemathesis.hook
        def before_call(context, case, **kwargs):
            raise RuntimeError("kaboom")

        stream = _examples_only(schema)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any("kaboom" in str(event.value) for event in errors), [str(e.value) for e in errors]


def test_examples_phase_translates_unsatisfiable_during_generation(ctx):
    stream = _hooked_examples_stream(ctx, _raise_unsatisfiable)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any(isinstance(event.value, hypothesis.errors.Unsatisfiable) for event in errors), [
        type(e.value).__name__ for e in errors
    ]


def test_examples_phase_translates_invalid_argument(ctx):
    stream = _hooked_examples_stream(ctx, _raise_invalid_argument)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any(isinstance(event.value, hypothesis.errors.InvalidArgument) for event in errors), [
        type(e.value).__name__ for e in errors
    ]


def test_examples_phase_translates_regex_validation_error(ctx):
    stream = _hooked_examples_stream(ctx, _raise_regex_validation_error)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any(isinstance(event.value, InvalidRegexPattern) for event in errors), [
        type(e.value).__name__ for e in errors
    ]


def test_examples_phase_translates_yaml_pattern_as_float(ctx):
    stream = _hooked_examples_stream(ctx, _raise_yaml_pattern_as_float_typeerror)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any(isinstance(event.value, InvalidRegexType) for event in errors), [type(e.value).__name__ for e in errors]


def test_examples_phase_handles_keyboard_interrupt_during_iteration(ctx):
    stream = _hooked_examples_stream(ctx, _raise_keyboard_interrupt)

    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.EXAMPLES)
    assert any(event.status == Status.INTERRUPTED for event in finished), [event.status for event in finished]
    assert stream.find_all(events.Interrupted, phase=PhaseName.EXAMPLES)


def test_examples_phase_fill_missing_fallback_defers_unsatisfiable(ctx):
    # Fill-missing fallback on an unsatisfiable schema surfaces as a deferred error, not a crash.
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {
                            "name": "key",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 5, "maximum": 4},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema.config.phases.examples.fill_missing = True

    stream = _examples_only(schema)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any(
        isinstance(event.value, hypothesis.errors.Unsatisfiable)
        and "Failed to generate test cases from examples" in str(event.value)
        for event in errors
    ), [type(e.value).__name__ for e in errors]
    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.EXAMPLES)
    assert any(event.status == Status.ERROR for event in finished), [event.status for event in finished]


def test_examples_phase_serialization_not_possible_wraps_message(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "parameters": [
                        {
                            "name": "key",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer"},
                            "example": 42,
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "image/jpeg": {
                                "schema": {"format": "base64", "type": "string"},
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    stream = _examples_only(schema)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any(
        isinstance(event.value, SerializationNotPossible)
        and "Failed to generate test cases from examples" in str(event.value)
        for event in errors
    ), [type(e.value).__name__ for e in errors]


def test_examples_phase_runs_when_error_feedback_disabled(ctx, app_runner):
    _, schema = _items_with_query_example(ctx, app_runner)
    schema.config.phases.fuzzing.error_feedback.is_enabled = False

    stream = _examples_only(schema)

    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.EXAMPLES)
    assert all(event.status == Status.SUCCESS for event in finished), [event.status for event in finished]


def test_examples_phase_applies_parameter_and_header_overrides(ctx, app_runner):
    paths = {
        "/items/{key}": {
            "get": {
                "parameters": [
                    {
                        "name": "key",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "minLength": 1},
                        "example": "alpha",
                    },
                    {
                        "name": "id",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                        "example": "beta",
                    },
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/items/<key>", methods=["GET"])
    def items(key):
        return ("", 200)

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))

    EventStream(
        schema,
        phases=[PhaseName.EXAMPLES],
        parameters={"key": "fixed", "id": "123"},
        headers={"X-Test": "yes"},
    ).execute()

    calls = [request for request in app.config["captured_requests"] if request.path.startswith("/items/")]
    assert calls
    assert all(request.headers.get("X-Test") == "yes" for request in calls)
    assert all(request.path == "/items/fixed" for request in calls)
    assert all(request.query.get("id") == "123" for request in calls)


def _raise_unsatisfiable():
    @hypothesis.given(st.integers().filter(lambda x: False))
    @hypothesis.settings(max_examples=1, suppress_health_check=list(hypothesis.HealthCheck))
    def t(_):
        pass

    t()


def _raise_invalid_argument():
    hypothesis.settings(max_examples=-1)


def _raise_regex_validation_error():
    make_validator_for({"type": "string", "pattern": "[unclosed"})


def _raise_non_regex_validation_error():
    make_validator_for({"type": 12345})


def _raise_yaml_pattern_as_float_typeerror():
    re.compile(12345)


def _raise_keyboard_interrupt():
    raise KeyboardInterrupt


def test_examples_phase_emits_missing_path_parameters_error(ctx):
    api = ctx.openapi.apps.missing_path_parameter()
    schema = schemathesis.openapi.from_url(api.schema_url)

    stream = _examples_only(schema)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any(
        isinstance(event.value, InvalidSchema) and "Path parameter 'id' is not defined" in str(event.value)
        for event in errors
    ), [str(e.value) for e in errors]
    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.EXAMPLES)
    assert any(event.status == Status.ERROR for event in finished), [event.status for event in finished]


def test_examples_phase_absorbs_non_regex_validation_error(ctx):
    # Non-regex validation errors are absorbed here; Coverage surfaces the schema-level signal.
    stream = _hooked_examples_stream(ctx, _raise_non_regex_validation_error)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert all(not isinstance(event.value, jsonschema_rs.ValidationError) for event in errors), [
        type(e.value).__name__ for e in errors
    ]
