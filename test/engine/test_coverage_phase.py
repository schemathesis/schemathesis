from __future__ import annotations

import re
from collections.abc import Callable

import hypothesis
import hypothesis.errors
import jsonschema_rs
from flask import request
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.errors import InvalidRegexPattern, InvalidRegexType, InvalidSchema
from schemathesis.core.jsonschema import make_validator_for
from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseName
from test.utils import EventStream


def _coverage_only(schema) -> EventStream:
    return EventStream(schema, phases=[PhaseName.COVERAGE]).execute()


def _hooked_coverage_stream(ctx, raise_naturally: Callable[[], None]) -> EventStream:
    # `raise_naturally` must throw via real execution — never `raise SomeException(...)`.
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @schema.hook
    def map_case(context, case):
        raise_naturally()
        return case

    return _coverage_only(schema)


def test_coverage_phase_skip_when_filter_drops_all_cases(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @schema.hook
    def filter_case(context, case):
        return False

    stream = _coverage_only(schema)

    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.COVERAGE)
    assert finished
    assert all(event.status == Status.SKIP for event in finished)
    assert all(event.skip_reason == "No examples in schema" for event in finished)


def test_coverage_phase_failure_status_for_check_failure(ctx):
    api = ctx.openapi.apps.failure()
    schema = schemathesis.openapi.from_url(api.schema_url)

    stream = _coverage_only(schema)

    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.COVERAGE)
    assert any(event.status == Status.FAILURE for event in finished)


def test_coverage_phase_authentication_error_emits_non_fatal_error(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @schemathesis.auth()
    class _BrokenAuth:
        def get(self, case, context):
            raise RuntimeError("provider blew up")

        def set(self, case, data, context):
            pass

    try:
        stream = _coverage_only(schema)
    finally:
        schemathesis.auths.unregister()

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.COVERAGE)
    assert errors, "expected at least one non-fatal error from auth failure"
    assert any("provider blew up" in str(event.value) for event in errors)
    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.COVERAGE)
    assert any(event.status == Status.ERROR for event in finished)


def test_coverage_phase_translates_generation_exception(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @schema.hook
    def map_case(context, case):
        raise RuntimeError("boom during generation")

    stream = _coverage_only(schema)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.COVERAGE)
    assert any("boom during generation" in str(event.value) for event in errors), [str(e.value) for e in errors]
    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.COVERAGE)
    assert any(event.status == Status.ERROR for event in finished)


def test_coverage_phase_drains_deduplicated_errors(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)

    with ctx.restore_hooks():

        @schemathesis.hook
        def before_call(context, case, **kwargs):
            raise RuntimeError("kaboom")

        stream = _coverage_only(schema)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.COVERAGE)
    assert any("kaboom" in str(event.value) for event in errors), [str(e.value) for e in errors]


def test_coverage_phase_emits_missing_path_parameters_error(ctx):
    api = ctx.openapi.apps.missing_path_parameter()
    schema = schemathesis.openapi.from_url(api.schema_url)

    stream = _coverage_only(schema)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.COVERAGE)
    assert any(
        isinstance(event.value, InvalidSchema) and "Path parameter 'id' is not defined" in str(event.value)
        for event in errors
    ), [str(e.value) for e in errors]
    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.COVERAGE)
    assert any(event.status == Status.ERROR for event in finished)


def test_coverage_phase_stops_iteration_when_max_failures_reached(ctx):
    api = ctx.openapi.apps.failure()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.max_failures = 1

    stream = EventStream(schema, phases=[PhaseName.COVERAGE], max_failures=1).execute()

    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.COVERAGE)
    failures = [event for event in finished if event.status == Status.FAILURE]
    assert len(failures) == 1
    assert len(failures[0].recorder.cases) >= 1


def test_coverage_phase_promotes_success_to_failure_on_continue_on_failure(ctx):
    # `continue_on_failure` records failed checks without raising — final status must still surface as FAILURE.
    api = ctx.openapi.apps.failure()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.continue_on_failure = True

    stream = _coverage_only(schema)

    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.COVERAGE)
    assert any(event.status == Status.FAILURE for event in finished), [event.status for event in finished]


def test_coverage_phase_runs_when_error_feedback_disabled(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.phases.fuzzing.error_feedback.is_enabled = False

    stream = _coverage_only(schema)

    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.COVERAGE)
    assert all(event.status == Status.SUCCESS for event in finished), [event.status for event in finished]


def test_coverage_phase_applies_parameter_and_header_overrides(ctx, app_runner):
    paths = {
        "/items/{key}": {
            "get": {
                "parameters": [
                    {
                        "name": "key",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "minLength": 1},
                    },
                    {
                        "name": "id",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "session",
                        "in": "cookie",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/items/<key>", methods=["GET"])
    def items(key):
        return "", 200

    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")

    EventStream(
        schema,
        phases=[PhaseName.COVERAGE],
        parameters={"key": "fixed", "id": "123", "session": "abc"},
        headers={"X-Test": "yes"},
    ).execute()

    calls = [request for request in app.config["captured_requests"] if request.path.startswith("/items/")]
    assert calls
    assert all(request.headers.get("X-Test") == "yes" for request in calls)
    assert all(request.path == "/items/fixed" for request in calls)
    assert all(request.query.get("id") == "123" for request in calls)
    assert all("session=abc" in (request.headers.get("Cookie") or "") for request in calls)


def test_coverage_phase_applies_override_when_container_is_absent(ctx, app_runner):
    @schemathesis.auth()
    class _ClearingAuth:
        def get(self, case, context):
            return "token"

        def set(self, case, data, context):
            case.query = None

    paths = {
        "/items": {
            "get": {
                "security": [{"bearer": []}],
                "parameters": [
                    {
                        "name": "tag",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    app, _ = ctx.openapi.make_flask_app(
        paths,
        components={"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
    )

    @app.route("/items", methods=["GET"])
    def items():
        return "", 200

    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")

    try:
        EventStream(schema, phases=[PhaseName.COVERAGE], parameters={"tag": "fixed"}).execute()
    finally:
        schemathesis.auths.unregister()

    calls = [request for request in app.config["captured_requests"] if request.path == "/items"]
    assert calls
    assert all(request.query.get("tag") == "fixed" for request in calls)


def test_coverage_phase_filter_case_hook_respects_filters(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    calls = []

    @schema.hook.apply_to(path_regex=r"/does-not-match")
    def filter_case(context, case):
        calls.append(case)
        return False

    stream = _coverage_only(schema)

    assert calls == []
    errors = stream.find_all(events.NonFatalError, phase=PhaseName.COVERAGE)
    assert not errors, [str(event.value) for event in errors]


def test_coverage_phase_map_case_hook_applies(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    calls = []

    @schema.hook
    def map_case(context, case):
        calls.append(case)
        return case

    stream = _coverage_only(schema)

    assert calls
    errors = stream.find_all(events.NonFatalError, phase=PhaseName.COVERAGE)
    assert not errors, [str(event.value) for event in errors]


def test_coverage_phase_map_case_hook_respects_filters(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    calls = []

    @schema.hook.apply_to(path_regex=r"/does-not-match")
    def map_case(context, case):
        calls.append(case)
        return case

    stream = _coverage_only(schema)

    assert calls == []
    errors = stream.find_all(events.NonFatalError, phase=PhaseName.COVERAGE)
    assert not errors, [str(event.value) for event in errors]


def test_unique_inputs_dedupes_repeated_explicit_examples(ctx, app_runner):
    # Three named examples with the same payload hit the server once with `unique_inputs=True`.
    paths = {
        "/items": {
            "get": {
                "parameters": [
                    {
                        "name": "kind",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                        "examples": {
                            "first": {"value": "alpha"},
                            "second": {"value": "alpha"},
                            "third": {"value": "alpha"},
                        },
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    captured: list[str] = []
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/items", methods=["GET"])
    def items():
        captured.append(request.query_string.decode())
        return ("", 200)

    port = app_runner.run_flask_app(app)

    def run(unique: bool) -> list[str]:
        captured.clear()
        schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
        # TODO: `phases.update(...)` resets per-phase generation defaults and clobbers user-set
        # `generation.unique_inputs`. Drop this manual disabling once that bug is fixed.
        schema.config.phases.coverage.enabled = False
        schema.config.phases.fuzzing.enabled = False
        schema.config.phases.stateful.enabled = False
        schema.config.generation.update(modes=[schemathesis.GenerationMode.POSITIVE], unique_inputs=unique)
        list(schemathesis.engine.from_schema(schema).execute())
        return list(captured)

    without_dedupe = run(unique=False)
    with_dedupe = run(unique=True)
    assert without_dedupe.count("kind=alpha") >= 3, without_dedupe
    assert with_dedupe.count("kind=alpha") == 1, with_dedupe


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


def test_coverage_phase_translates_unsatisfiable(ctx):
    stream = _hooked_coverage_stream(ctx, _raise_unsatisfiable)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.COVERAGE)
    assert any(isinstance(event.value, hypothesis.errors.Unsatisfiable) for event in errors), [
        type(e.value).__name__ for e in errors
    ]


def test_coverage_phase_translates_invalid_argument(ctx):
    stream = _hooked_coverage_stream(ctx, _raise_invalid_argument)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.COVERAGE)
    assert any(isinstance(event.value, hypothesis.errors.InvalidArgument) for event in errors), [
        type(e.value).__name__ for e in errors
    ]


def test_coverage_phase_translates_regex_validation_error(ctx):
    stream = _hooked_coverage_stream(ctx, _raise_regex_validation_error)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.COVERAGE)
    assert any(isinstance(event.value, InvalidRegexPattern) for event in errors), [
        type(e.value).__name__ for e in errors
    ]


def test_coverage_phase_translates_non_regex_validation_error(ctx):
    stream = _hooked_coverage_stream(ctx, _raise_non_regex_validation_error)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.COVERAGE)
    assert any(
        isinstance(event.value, jsonschema_rs.ValidationError) and not isinstance(event.value, InvalidRegexPattern)
        for event in errors
    ), [type(e.value).__name__ for e in errors]


def test_coverage_phase_translates_yaml_pattern_as_float(ctx):
    stream = _hooked_coverage_stream(ctx, _raise_yaml_pattern_as_float_typeerror)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.COVERAGE)
    assert any(isinstance(event.value, InvalidRegexType) for event in errors), [type(e.value).__name__ for e in errors]


def test_coverage_phase_handles_keyboard_interrupt_during_iteration(ctx):
    stream = _hooked_coverage_stream(ctx, _raise_keyboard_interrupt)

    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.COVERAGE)
    assert any(event.status == Status.INTERRUPTED for event in finished), [event.status for event in finished]
    assert stream.find_all(events.Interrupted, phase=PhaseName.COVERAGE)


def test_coverage_phase_negative_multiple_of_with_float_bounds(ctx, app_runner):
    # Negating `multipleOf` next to float bounds used to build a schema with a duplicated `type` list.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "number", "multipleOf": 0.1, "minimum": 0.1, "maximum": 1}
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))

    stream = EventStream(schema, phases=[PhaseName.COVERAGE], modes=[schemathesis.GenerationMode.NEGATIVE]).execute()

    assert stream.find_all(events.NonFatalError) == []
