from __future__ import annotations

import json
import uuid

import pytest
import requests
from hypothesis import assume
from hypothesis.errors import Flaky, Unsatisfiable
from werkzeug.exceptions import InternalServerError

import schemathesis
import schemathesis.auths
from schemathesis.config import FuzzConfig, OperationConfig, OperationsConfig
from schemathesis.core.errors import SerializationNotPossible
from schemathesis.core.result import Ok
from schemathesis.core.transport import Response
from schemathesis.engine import Status, StopReason, events, from_schema
from schemathesis.generation import GenerationMode
from schemathesis.specs.openapi.stateful._link_chooser import collect_link_candidates


def _make_flaky_repro_schema(ctx):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/failure": {
                "get": {
                    "responses": {
                        "200": {"description": "OK"},
                    }
                }
            },
            "/csv": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "text/csv": {
                                "schema": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {
                                            "first_name": {"type": "string"},
                                            "last_name": {"type": "string"},
                                        },
                                        "required": ["first_name", "last_name"],
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "OK"},
                    },
                }
            },
        }
    )

    @app.route("/failure", methods=["GET"])
    def failure():
        raise InternalServerError

    @app.route("/csv", methods=["POST"])
    def csv():
        return {"ok": True}

    return app


def _fuzz_events(schema, config=None):
    stream = from_schema(schema).fuzz(config) if config is not None else from_schema(schema).fuzz()
    return list(stream)


def _collect_until_finished(schema, *, count=1, config=None):
    collected: list[events.EngineEvent] = []
    stream = from_schema(schema).fuzz(config) if config is not None else from_schema(schema).fuzz()
    for event in stream:
        collected.append(event)
        if isinstance(event, events.FuzzScenarioFinished) and len(_finished_scenarios(collected)) >= count:
            stream.stop()
            break
    return collected


def _assert_engine_started_and_finished(collected):
    assert isinstance(collected[0], events.EngineStarted)
    assert isinstance(collected[-1], events.EngineFinished)


def _finished_scenarios(collected, *, status=None):
    finished = [event for event in collected if isinstance(event, events.FuzzScenarioFinished)]
    if status is not None:
        return [event for event in finished if event.status == status]
    return finished


def _assert_scenario_pairs(collected):
    started_ids: set[uuid.UUID] = set()
    finished_ids: list[uuid.UUID] = []
    for event in collected:
        if isinstance(event, events.FuzzScenarioStarted):
            started_ids.add(event.id)
        elif isinstance(event, events.FuzzScenarioFinished):
            assert event.id in started_ids
            finished_ids.append(event.id)
    assert started_ids
    assert finished_ids
    return finished_ids


def _non_fatal_errors(collected):
    return [event for event in collected if isinstance(event, events.NonFatalError)]


def test_fuzz_entry_point_emits_engine_started_and_finished(ctx):
    api = ctx.openapi.apps.success_and_failure()
    schema = schemathesis.openapi.from_url(api.schema_url)
    _assert_engine_started_and_finished(_fuzz_events(schema))


def test_fuzz_scenario_events_are_paired(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    collected = _collect_until_finished(schema)
    _assert_scenario_pairs(collected)


def test_fuzz_no_operations_emits_no_scenarios(ctx):
    # When the schema has no operations, the fuzz thread returns immediately
    schema = ctx.openapi.load_schema({})
    collected = _fuzz_events(schema)
    _assert_engine_started_and_finished(collected)
    assert not any(isinstance(e, events.FuzzScenarioStarted) for e in collected)


def test_fuzz_single_unsatisfiable_operation_emits_non_fatal_error(ctx):
    api = ctx.openapi.apps.unsatisfiable()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When the only operation is unsatisfiable in all configured modes, a NonFatalError should be emitted.
    # Use positive-only mode: the unsatisfiable schema has contradictory positive constraints,
    # so negative mode would succeed. We want to test the "truly unsatisfiable" path.
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])
    collected = _fuzz_events(schema, FuzzConfig())
    assert _non_fatal_errors(collected)


def test_fuzz_finds_failure_without_continue_on_failure(ctx):
    api = ctx.openapi.apps.failure()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # Without continue_on_failure, the first failure stops the campaign
    collected = _fuzz_events(schema, FuzzConfig())
    assert _finished_scenarios(collected, status=Status.FAILURE)
    _assert_engine_started_and_finished(collected)


def test_fuzz_preflight_excludes_non_generatable_operation_before_hypothesis(ctx, app_runner):
    app = _make_flaky_repro_schema(ctx)
    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    schema.config.seed = 1

    collected = _fuzz_events(schema, FuzzConfig())
    errors = _non_fatal_errors(collected)
    finished = _finished_scenarios(collected)

    assert any(isinstance(event.value, SerializationNotPossible) and event.label == "POST /csv" for event in errors)
    assert not any(isinstance(event.value, Flaky) for event in errors)
    assert any(event.status == Status.FAILURE for event in finished)


def test_fuzz_preflight_all_operations_excluded(ctx):
    api = ctx.openapi.apps.success_and_failure()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @schema.hook
    def before_generate_case(context, strategy):
        def reject(case):
            assume(False)

        return strategy.map(reject)

    collected = _fuzz_events(schema, FuzzConfig())

    _assert_engine_started_and_finished(collected)
    assert not any(isinstance(event, events.FuzzScenarioStarted) for event in collected)
    assert not _finished_scenarios(collected)
    assert _non_fatal_errors(collected)


def test_fuzz_finds_failure_with_continue_on_failure(ctx):
    api = ctx.openapi.apps.multiple_failures()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # With continue_on_failure, the campaign continues past failures;
    # multiple_failures has an integer query param so Hypothesis generates many examples
    schema.config.continue_on_failure = True
    collected = _collect_until_finished(schema, count=3, config=FuzzConfig())
    finished = _finished_scenarios(collected)
    # Campaign continued past the first failure
    assert len(finished) >= 3
    # At least one failure was recorded
    assert _finished_scenarios(collected, status=Status.FAILURE)


def test_fuzz_max_time_stop_reason(ctx):
    api = ctx.openapi.apps.path_variable()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])
    collected = _fuzz_events(schema, FuzzConfig(max_time=1))
    _assert_engine_started_and_finished(collected)
    assert collected[-1].stop_reason == StopReason.MAX_TIME


def test_fuzz_max_failures_stop_reason(ctx):
    api = ctx.openapi.apps.failure()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.max_failures = 1
    collected = _fuzz_events(schema, FuzzConfig())
    assert collected[-1].stop_reason == StopReason.FAILURE_LIMIT


def test_fuzz_max_failures_multi_worker(ctx):
    api = ctx.openapi.apps.multiple_failures()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # With 2 workers and continue_on_failure, the global failure counter stops the campaign
    schema.config.max_failures = 3
    schema.config.continue_on_failure = True
    schema.config.update(workers=2)
    collected = _fuzz_events(schema, FuzzConfig())
    assert collected[-1].stop_reason == StopReason.FAILURE_LIMIT


def test_fuzz_scenario_id_pairs_correlate(ctx):
    api = ctx.openapi.apps.path_variable()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])
    collected = _collect_until_finished(schema, count=3, config=FuzzConfig())
    finished_ids = _assert_scenario_pairs(collected)
    assert len(finished_ids) == 3


def test_fuzz_operation_weights_producer_higher_than_consumer(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}},
                                        "required": ["id"],
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/users/{user_id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    operations = [op.ok() for op in schema.get_all_operations() if isinstance(op, Ok)]
    weights = schema.compute_fuzz_operation_weights(operations)

    # Producer (POST /users, layer 0 with outputs) must outweigh consumer (GET, deeper layer)
    assert weights["POST /users"] > weights["GET /users/{user_id}"]


def test_fuzz_per_operation_continue_on_failure(ctx):
    api = ctx.openapi.apps.multiple_failures()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When continue_on_failure=True is set only for a specific operation (not globally)
    schema.config.operations = OperationsConfig(
        operations=[OperationConfig.from_dict({"include-path": "/api/multiple_failures", "continue-on-failure": True})]
    )
    collected = _collect_until_finished(schema, count=3, config=FuzzConfig())
    finished = _finished_scenarios(collected)
    # Then fuzz continues past failures (does not raise) and records them
    assert len(finished) >= 3
    assert _finished_scenarios(collected, status=Status.FAILURE)


def test_fuzz_generation_parameter_overrides_are_applied(ctx):
    api = ctx.openapi.apps.multiple_failures()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When a parameter override is configured
    schema.config.parameters = {"query.id": -1}
    stream = from_schema(schema).fuzz(FuzzConfig())
    query_values: list[dict] = []
    for event in stream:
        if isinstance(event, events.FuzzScenarioFinished):
            for node in event.recorder.cases.values():
                query_values.append(node.value.query or {})
            if len(query_values) >= 3:
                stream.stop()
                break
    # Then every generated case must have the fixed id value, not a random one
    assert query_values == [{"id": -1}] * len(query_values)


def test_fuzz_multiple_workers_emit_distinct_worker_ids(ctx):
    api = ctx.openapi.apps.path_variable()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # Each worker emits events with its own worker_id; with workers=2 we must see both 0 and 1
    schema.config.update(workers=2)
    worker_ids: set[int] = set()
    stream = from_schema(schema).fuzz(FuzzConfig())
    for event in stream:
        if isinstance(event, events.FuzzScenarioFinished):
            worker_ids.add(event.worker_id)
            if worker_ids == {0, 1}:
                stream.stop()
                break
    assert worker_ids == {0, 1}


@pytest.mark.usefixtures("restore_checks")
def test_fuzz_scenario_interrupted_status(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @schemathesis.check
    def always_interrupts(ctx, response, case):
        raise KeyboardInterrupt

    collected = _fuzz_events(schema, FuzzConfig())
    assert _finished_scenarios(collected, status=Status.INTERRUPTED)


@pytest.mark.usefixtures("restore_checks")
def test_fuzz_scenario_error_status(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When a check raises an unexpected exception, the scenario must finish with ERROR status.

    @schemathesis.check
    def always_errors(ctx, response, case):
        raise RuntimeError("deliberate error")

    collected = _fuzz_events(schema, FuzzConfig())
    assert _finished_scenarios(collected, status=Status.ERROR)


def test_fuzz_graphql_schema(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    collected = _collect_until_finished(schema, count=3, config=FuzzConfig())
    finished_ids = _assert_scenario_pairs(collected)
    assert isinstance(collected[0], events.EngineStarted)
    assert len(finished_ids) >= 3


def test_fuzz_unsatisfied_assumption_preflight_excludes_operation(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @schema.hook
    def before_generate_case(context, strategy):
        def reject(case):
            assume(False)

        return strategy.map(reject)

    collected = _fuzz_events(schema, FuzzConfig())
    errors = _non_fatal_errors(collected)
    assert errors == [
        events.NonFatalError(error=Unsatisfiable(), phase=None, label="GET /api/success", related_to_operation=True)
    ]


def test_fuzz_clears_global_auth_when_config_auth_is_defined(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @schemathesis.auth()
    class _TestAuth:
        def get(self, case, context):
            return "token"

        def set(self, case, data, context):
            pass

    assert schemathesis.auths.GLOBAL_AUTH_STORAGE.is_defined
    schema.config.auth.update(basic=("user", "pass"))
    next(iter(from_schema(schema).fuzz(FuzzConfig())))
    assert not schemathesis.auths.GLOBAL_AUTH_STORAGE.is_defined


def test_fuzz_generation_exception_excludes_operation(ctx):
    api = ctx.openapi.apps.path_variable()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @schema.hook
    def before_generate_case(context, strategy):
        def fail_generation(case):
            raise RuntimeError("generation error")

        return strategy.map(fail_generation)

    collected = _fuzz_events(schema, FuzzConfig())
    errors = _non_fatal_errors(collected)
    assert errors == [
        events.NonFatalError(
            error=RuntimeError("generation error"),
            phase=None,
            label="GET /api/path_variable/{key}",
            related_to_operation=True,
        )
    ]


def test_fuzz_preflight_excludes_hook_broken_operation_but_keeps_survivors(ctx):
    api = ctx.openapi.apps.success_and_failure()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @schema.hook
    def before_generate_case(context, strategy):
        def fail_generation(case):
            if case.operation.label == "GET /api/failure":
                raise RuntimeError("generation error")
            return case

        return strategy.map(fail_generation)

    collected = _collect_until_finished(schema, config=FuzzConfig())

    errors = _non_fatal_errors(collected)
    assert any(
        event
        == events.NonFatalError(
            error=RuntimeError("generation error"),
            phase=None,
            label="GET /api/failure",
            related_to_operation=True,
        )
        for event in errors
    )
    assert _finished_scenarios(collected)


def test_fuzz_preflight_reports_exclusion_once_across_workers(ctx, app_runner):
    app = _make_flaky_repro_schema(ctx)
    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    schema.config.seed = 1
    schema.config.update(workers=2)

    collected = _fuzz_events(schema, FuzzConfig())
    errors = _non_fatal_errors(collected)
    csv_errors = [
        event for event in errors if event.label == "POST /csv" and isinstance(event.value, SerializationNotPossible)
    ]

    assert len(csv_errors) == 1
    assert not any(isinstance(event.value, Flaky) for event in errors)
    assert _finished_scenarios(collected, status=Status.FAILURE)


def test_fuzz_network_error_emits_non_fatal_error(ctx):
    api = ctx.openapi.apps.slow()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When the server times out, a NonFatalError is emitted and the scenario still finishes
    schema.config.request_timeout = 0.001
    collected = _fuzz_events(schema, FuzzConfig())
    errors = _non_fatal_errors(collected)
    assert errors == [
        events.NonFatalError(
            error=requests.exceptions.ReadTimeout(),
            phase=None,
            label="GET /api/slow",
            related_to_operation=True,
        )
    ]
    _assert_engine_started_and_finished(collected)


def test_fuzz_finishes_cleanly_when_preflight_excludes_all_operations(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @schema.hook
    def before_generate_case(context, strategy):
        def fail_generation(case):
            raise RuntimeError("generation error")

        return strategy.map(fail_generation)

    collected = _fuzz_events(schema, FuzzConfig())
    _assert_engine_started_and_finished(collected)


def test_fuzz_emits_no_scenario_pairs_when_preflight_excludes_only_operation(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When preflight excludes the only operation before any scenario starts,
    # there should be no unmatched fuzz scenario events.

    @schema.hook
    def before_generate_case(context, strategy):
        def fail_generation(case):
            raise RuntimeError("generation error")

        return strategy.map(fail_generation)

    collected = _fuzz_events(schema, FuzzConfig())
    started_ids = set()
    finished_ids = set()
    for event in collected:
        if isinstance(event, events.FuzzScenarioStarted):
            started_ids.add(event.id)
        elif isinstance(event, events.FuzzScenarioFinished):
            finished_ids.add(event.id)
    assert started_ids == finished_ids


def test_fuzz_completed_stop_reason_when_preflight_excludes_all_operations(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When preflight excludes all operations due to generation errors, the engine should finish
    # with COMPLETED (natural end), not INTERRUPTED (which implies external stop).

    @schema.hook
    def before_generate_case(context, strategy):
        def fail_generation(case):
            raise RuntimeError("generation error")

        return strategy.map(fail_generation)

    collected = _fuzz_events(schema, FuzzConfig())
    finished = next(e for e in collected if isinstance(e, events.EngineFinished))
    assert finished.stop_reason == StopReason.COMPLETED


def test_fuzz_interrupted_by_keyboard_interrupt(ctx):
    api = ctx.openapi.apps.path_variable()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # Inject KeyboardInterrupt directly into the generator — safe under xdist (no OS signals)
    stream = from_schema(schema).fuzz(FuzzConfig())
    assert isinstance(next(stream), events.EngineStarted)
    # Advance into run_forever so the generator is suspended at a yield inside it
    next(stream)
    # Throw KeyboardInterrupt at the current yield suspension point
    interrupted = stream.generator.throw(KeyboardInterrupt)
    assert isinstance(interrupted, events.Interrupted)
    # Drain to EngineFinished
    for _ in stream:
        pass


@pytest.mark.usefixtures("restore_checks")
def test_fuzz_runs_class_based_checks(ctx):
    seen = []

    @schemathesis.check
    class Recorder:
        def after_response(self, ctx, response, case):
            seen.append(case.operation.label)

        def after_run(self, ctx):
            raise AssertionError("after_run fired")

    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["Recorder"])

    stream = from_schema(schema).fuzz(FuzzConfig())
    collected: list[events.EngineEvent] = []
    stopped = False
    for event in stream:
        collected.append(event)
        if isinstance(event, events.FuzzScenarioFinished) and not stopped:
            stream.stop()
            stopped = True

    finished = collected[-1]
    assert isinstance(finished, events.EngineFinished)
    assert seen, "after_response did not run during fuzzing"
    assert [f.message for f in finished.failures] == ["after_run fired"]


def _build_schema_with_link(ctx):
    return ctx.openapi.load_schema(
        {
            "/products": {
                "post": {
                    "operationId": "createProduct",
                    "responses": {
                        "201": {
                            "description": "C",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"productId": {"type": "string"}},
                                    }
                                }
                            },
                            "links": {
                                "GetProduct": {
                                    "operationId": "getProduct",
                                    "parameters": {"productId": "$response.body#/productId"},
                                }
                            },
                        }
                    },
                }
            },
            "/products/{productId}": {
                "get": {
                    "operationId": "getProduct",
                    "parameters": [{"name": "productId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                }
            },
        }
    )


@pytest.mark.parametrize(
    ("active", "exclude_get", "body", "expected_overrides"),
    [
        pytest.param(
            ("post", "get"),
            False,
            {"productId": "abc-123"},
            {"path_parameters": {"productId": "abc-123"}},
            id="resolved-target",
        ),
        pytest.param(("post", "get"), True, {"productId": "abc-123"}, None, id="target-excluded"),
        pytest.param(("post",), False, {"productId": "abc-123"}, None, id="target-not-in-active-set"),
        pytest.param(("post", "get"), False, {"otherField": "x"}, None, id="expression-unresolvable"),
    ],
)
def test_collect_link_candidates(ctx, active, exclude_get, body, expected_overrides, case_factory, response_factory):
    schema = _build_schema_with_link(ctx)
    post_op = schema["/products"]["POST"]
    get_op = schema["/products/{productId}"]["GET"]
    by_alias = {"post": post_op, "get": get_op}
    operations_by_label = {by_alias[a].label: by_alias[a] for a in active}
    excluded_labels = {get_op.label} if exclude_get else set()
    case = case_factory(operation=post_op)
    response = Response.from_requests(
        response_factory.requests(status_code=201, content=json.dumps(body).encode()),
        verify=True,
    )
    candidates = collect_link_candidates(
        operation=post_op,
        case=case,
        response=response,
        operations_by_label=operations_by_label,
        excluded_labels=excluded_labels,
    )
    if expected_overrides is None:
        assert candidates == []
    else:
        assert len(candidates) == 1
        target, overrides = candidates[0]
        assert target.label == get_op.label
        assert overrides == expected_overrides
