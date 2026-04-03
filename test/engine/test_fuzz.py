from __future__ import annotations

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
from schemathesis.engine import Status, StopReason, events, from_schema
from schemathesis.engine.fuzz._executor import compute_operation_weights


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


def test_fuzz_entry_point_emits_engine_started_and_finished(real_app_schema):
    stream = from_schema(real_app_schema).fuzz()
    collected = list(stream)
    assert isinstance(collected[0], events.EngineStarted)
    assert isinstance(collected[-1], events.EngineFinished)


@pytest.mark.operations("success")
def test_fuzz_scenario_events_are_paired(real_app_schema):
    started_ids: set[uuid.UUID] = set()
    finished_ids: list[uuid.UUID] = []
    stream = from_schema(real_app_schema).fuzz()
    for event in stream:
        if isinstance(event, events.FuzzScenarioStarted):
            started_ids.add(event.id)
        elif isinstance(event, events.FuzzScenarioFinished):
            assert event.id in started_ids
            finished_ids.append(event.id)
            stream.stop()
            break
    assert started_ids
    assert finished_ids


@pytest.mark.operations()
def test_fuzz_no_operations_emits_no_scenarios(real_app_schema):
    # When the schema has no operations, the fuzz thread returns immediately
    collected = list(from_schema(real_app_schema).fuzz())
    assert isinstance(collected[0], events.EngineStarted)
    assert isinstance(collected[-1], events.EngineFinished)
    assert not any(isinstance(e, events.FuzzScenarioStarted) for e in collected)


@pytest.mark.operations("unsatisfiable")
def test_fuzz_single_unsatisfiable_operation_emits_non_fatal_error(real_app_schema):
    # When the only operation is unsatisfiable, a NonFatalError should be emitted
    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1)))
    assert any(isinstance(e, events.NonFatalError) for e in collected)


@pytest.mark.operations("failure")
def test_fuzz_finds_failure_without_continue_on_failure(real_app_schema):
    # Without continue_on_failure, the first failure stops the campaign
    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1)))
    finished = [e for e in collected if isinstance(e, events.FuzzScenarioFinished)]
    assert any(e.status == Status.FAILURE for e in finished)
    assert isinstance(collected[-1], events.EngineFinished)


def test_fuzz_preflight_excludes_non_generatable_operation_before_hypothesis(ctx, app_runner):
    app = _make_flaky_repro_schema(ctx)
    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    schema.config.seed = 1

    collected = list(from_schema(schema).fuzz(FuzzConfig(max_steps=2)))
    errors = [event for event in collected if isinstance(event, events.NonFatalError)]
    finished = [event for event in collected if isinstance(event, events.FuzzScenarioFinished)]

    assert any(isinstance(event.value, SerializationNotPossible) and event.label == "POST /csv" for event in errors)
    assert not any(isinstance(event.value, Flaky) for event in errors)
    assert any(event.status == Status.FAILURE for event in finished)


@pytest.mark.operations("success", "failure")
def test_fuzz_preflight_all_operations_excluded(real_app_schema):
    @real_app_schema.hook
    def before_generate_case(context, strategy):
        def reject(case):
            assume(False)

        return strategy.map(reject)

    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1)))

    assert isinstance(collected[0], events.EngineStarted)
    assert isinstance(collected[-1], events.EngineFinished)
    assert not any(isinstance(event, events.FuzzScenarioStarted) for event in collected)
    assert not any(isinstance(event, events.FuzzScenarioFinished) for event in collected)
    assert any(isinstance(event, events.NonFatalError) for event in collected)


@pytest.mark.operations("multiple_failures")
def test_fuzz_finds_failure_with_continue_on_failure(real_app_schema):
    # With continue_on_failure, the campaign continues past failures;
    # multiple_failures has an integer query param so Hypothesis generates many examples
    real_app_schema.config.continue_on_failure = True
    stream = from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1))
    scenario_count = 0
    failure_count = 0
    for event in stream:
        if isinstance(event, events.FuzzScenarioFinished):
            scenario_count += 1
            if event.status == Status.FAILURE:
                failure_count += 1
            if scenario_count >= 3:
                stream.stop()
                break
    # Campaign continued past the first failure
    assert scenario_count >= 3
    # At least one failure was recorded
    assert failure_count >= 1


@pytest.mark.operations("path_variable")
def test_fuzz_max_time_stop_reason(real_app_schema):
    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_time=1, max_steps=1)))
    assert isinstance(collected[0], events.EngineStarted)
    assert isinstance(collected[-1], events.EngineFinished)
    assert collected[-1].stop_reason == StopReason.MAX_TIME


@pytest.mark.operations("failure")
def test_fuzz_max_failures_stop_reason(real_app_schema):
    real_app_schema.config.max_failures = 1
    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1)))
    assert collected[-1].stop_reason == StopReason.FAILURE_LIMIT


@pytest.mark.operations("multiple_failures")
def test_fuzz_max_failures_multi_worker(real_app_schema):
    # With 2 workers and continue_on_failure, the global failure counter stops the campaign
    real_app_schema.config.max_failures = 3
    real_app_schema.config.continue_on_failure = True
    real_app_schema.config.update(workers=2)
    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1)))
    assert collected[-1].stop_reason == StopReason.FAILURE_LIMIT


@pytest.mark.operations("path_variable")
def test_fuzz_scenario_id_pairs_correlate(real_app_schema):
    started_ids: set[uuid.UUID] = set()
    finished_ids: list[uuid.UUID] = []
    stream = from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1))
    for event in stream:
        if isinstance(event, events.FuzzScenarioStarted):
            started_ids.add(event.id)
        elif isinstance(event, events.FuzzScenarioFinished):
            assert event.id in started_ids
            finished_ids.append(event.id)
            if len(finished_ids) == 3:
                stream.stop()
                break
    assert len(finished_ids) == 3


def test_fuzz_operation_weights_producer_higher_than_consumer(ctx):
    schema = ctx.openapi.build_schema(
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
    loaded = schemathesis.openapi.from_dict(schema)
    operations = [op.ok() for op in loaded.get_all_operations() if isinstance(op, Ok)]
    weights = compute_operation_weights(loaded, operations)

    # Producer (POST /users, layer 0 with outputs) must outweigh consumer (GET, deeper layer)
    assert weights["POST /users"] > weights["GET /users/{user_id}"]


@pytest.mark.operations("multiple_failures")
def test_fuzz_per_operation_continue_on_failure(real_app_schema):
    # When continue_on_failure=True is set only for a specific operation (not globally)
    real_app_schema.config.operations = OperationsConfig(
        operations=[OperationConfig.from_dict({"include-path": "/multiple_failures", "continue-on-failure": True})]
    )
    stream = from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1))
    scenario_count = 0
    failure_count = 0
    for event in stream:
        if isinstance(event, events.FuzzScenarioFinished):
            scenario_count += 1
            if event.status == Status.FAILURE:
                failure_count += 1
            if scenario_count >= 3:
                stream.stop()
                break
    # Then fuzz continues past failures (does not raise) and records them
    assert scenario_count >= 3
    assert failure_count >= 1


@pytest.mark.operations("multiple_failures")
def test_fuzz_generation_parameter_overrides_are_applied(real_app_schema):
    # When a parameter override is configured
    real_app_schema.config.parameters = {"query.id": -1}
    stream = from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1))
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


@pytest.mark.operations("path_variable")
def test_fuzz_multiple_workers_emit_distinct_worker_ids(real_app_schema):
    # Each worker emits events with its own worker_id; with workers=2 we must see both 0 and 1
    real_app_schema.config.update(workers=2)
    worker_ids: set[int] = set()
    stream = from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1))
    for event in stream:
        if isinstance(event, events.FuzzScenarioFinished):
            worker_ids.add(event.worker_id)
            if worker_ids == {0, 1}:
                stream.stop()
                break
    assert worker_ids == {0, 1}


@pytest.mark.operations("success")
@pytest.mark.usefixtures("restore_checks")
def test_fuzz_scenario_interrupted_status(real_app_schema):
    @schemathesis.check
    def always_interrupts(ctx, response, case):
        raise KeyboardInterrupt

    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1)))
    assert any(isinstance(e, events.FuzzScenarioFinished) and e.status == Status.INTERRUPTED for e in collected)


@pytest.mark.operations("success")
@pytest.mark.usefixtures("restore_checks")
def test_fuzz_scenario_error_status(real_app_schema):
    # When a check raises an unexpected exception, the scenario must finish with ERROR status.
    @schemathesis.check
    def always_errors(ctx, response, case):
        raise RuntimeError("deliberate error")

    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1)))
    assert any(isinstance(e, events.FuzzScenarioFinished) and e.status == Status.ERROR for e in collected)


def test_fuzz_graphql_schema(graphql_schema):
    collected: list[events.EngineEvent] = []
    started_ids: set[uuid.UUID] = set()
    finished_ids: list[uuid.UUID] = []
    stream = from_schema(graphql_schema).fuzz(FuzzConfig(max_steps=1))
    for event in stream:
        collected.append(event)
        if isinstance(event, events.FuzzScenarioStarted):
            started_ids.add(event.id)
        elif isinstance(event, events.FuzzScenarioFinished):
            assert event.id in started_ids
            finished_ids.append(event.id)
            if len(finished_ids) >= 3:
                stream.stop()
                break
    assert isinstance(collected[0], events.EngineStarted)
    assert len(finished_ids) >= 3


@pytest.mark.operations("success")
def test_fuzz_unsatisfied_assumption_preflight_excludes_operation(real_app_schema):
    @real_app_schema.hook
    def before_generate_case(context, strategy):
        def reject(case):
            assume(False)

        return strategy.map(reject)

    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1)))
    errors = [e for e in collected if isinstance(e, events.NonFatalError)]
    assert errors == [
        events.NonFatalError(error=Unsatisfiable(), phase=None, label="GET /success", related_to_operation=True)
    ]


@pytest.mark.operations("success")
def test_fuzz_clears_global_auth_when_config_auth_is_defined(real_app_schema):
    @schemathesis.auth()
    class _TestAuth:
        def get(self, case, context):
            return "token"

        def set(self, case, data, context):
            pass

    assert schemathesis.auths.GLOBAL_AUTH_STORAGE.is_defined
    real_app_schema.config.auth.update(basic=("user", "pass"))
    next(iter(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1))))
    assert not schemathesis.auths.GLOBAL_AUTH_STORAGE.is_defined


@pytest.mark.operations("path_variable")
def test_fuzz_generation_exception_excludes_operation(real_app_schema):
    @real_app_schema.hook
    def before_generate_case(context, strategy):
        def fail_generation(case):
            raise RuntimeError("generation error")

        return strategy.map(fail_generation)

    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1)))
    errors = [e for e in collected if isinstance(e, events.NonFatalError)]
    assert errors == [
        events.NonFatalError(
            error=RuntimeError("generation error"),
            phase=None,
            label="GET /path_variable/{key}",
            related_to_operation=True,
        )
    ]


@pytest.mark.operations("success", "failure")
def test_fuzz_preflight_excludes_hook_broken_operation_but_keeps_survivors(real_app_schema):
    @real_app_schema.hook
    def before_generate_case(context, strategy):
        def fail_generation(case):
            if case.operation.label == "GET /failure":
                raise RuntimeError("generation error")
            return case

        return strategy.map(fail_generation)

    collected: list[events.EngineEvent] = []
    stream = from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1))
    for event in stream:
        collected.append(event)
        if isinstance(event, events.FuzzScenarioFinished):
            stream.stop()
            break

    errors = [event for event in collected if isinstance(event, events.NonFatalError)]
    assert any(
        event
        == events.NonFatalError(
            error=RuntimeError("generation error"),
            phase=None,
            label="GET /failure",
            related_to_operation=True,
        )
        for event in errors
    )
    assert any(isinstance(event, events.FuzzScenarioFinished) for event in collected)


def test_fuzz_preflight_reports_exclusion_once_across_workers(ctx, app_runner):
    app = _make_flaky_repro_schema(ctx)
    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    schema.config.seed = 1
    schema.config.update(workers=2)

    collected = list(from_schema(schema).fuzz(FuzzConfig(max_steps=2)))
    errors = [event for event in collected if isinstance(event, events.NonFatalError)]
    csv_errors = [
        event for event in errors if event.label == "POST /csv" and isinstance(event.value, SerializationNotPossible)
    ]

    assert len(csv_errors) == 1
    assert not any(isinstance(event.value, Flaky) for event in errors)
    assert any(isinstance(event, events.FuzzScenarioFinished) and event.status == Status.FAILURE for event in collected)


@pytest.mark.operations("slow")
def test_fuzz_network_error_emits_non_fatal_error(real_app_schema):
    # When the server times out, a NonFatalError is emitted and the scenario still finishes
    real_app_schema.config.request_timeout = 0.001
    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1)))
    errors = [e for e in collected if isinstance(e, events.NonFatalError)]
    assert errors == [
        events.NonFatalError(
            error=requests.exceptions.ReadTimeout(),
            phase=None,
            label="GET /slow",
            related_to_operation=True,
        )
    ]
    assert isinstance(collected[-1], events.EngineFinished)


@pytest.mark.operations("success")
def test_fuzz_finishes_cleanly_when_preflight_excludes_all_operations(real_app_schema):
    @real_app_schema.hook
    def before_generate_case(context, strategy):
        def fail_generation(case):
            raise RuntimeError("generation error")

        return strategy.map(fail_generation)

    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=2)))
    assert isinstance(collected[0], events.EngineStarted)
    assert isinstance(collected[-1], events.EngineFinished)


@pytest.mark.operations("success")
def test_fuzz_emits_no_scenario_pairs_when_preflight_excludes_only_operation(real_app_schema):
    # When preflight excludes the only operation before any scenario starts,
    # there should be no unmatched fuzz scenario events.
    @real_app_schema.hook
    def before_generate_case(context, strategy):
        def fail_generation(case):
            raise RuntimeError("generation error")

        return strategy.map(fail_generation)

    started_ids: set[uuid.UUID] = set()
    finished_ids: set[uuid.UUID] = set()
    for event in from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=2)):
        if isinstance(event, events.FuzzScenarioStarted):
            started_ids.add(event.id)
        elif isinstance(event, events.FuzzScenarioFinished):
            finished_ids.add(event.id)
    assert started_ids == finished_ids


@pytest.mark.operations("success")
def test_fuzz_completed_stop_reason_when_preflight_excludes_all_operations(real_app_schema):
    # When preflight excludes all operations due to generation errors, the engine should finish
    # with COMPLETED (natural end), not INTERRUPTED (which implies external stop).
    @real_app_schema.hook
    def before_generate_case(context, strategy):
        def fail_generation(case):
            raise RuntimeError("generation error")

        return strategy.map(fail_generation)

    collected = list(from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=2)))
    finished = next(e for e in collected if isinstance(e, events.EngineFinished))
    assert finished.stop_reason == StopReason.COMPLETED


@pytest.mark.operations("path_variable")
def test_fuzz_interrupted_by_keyboard_interrupt(real_app_schema):
    # Inject KeyboardInterrupt directly into the generator — safe under xdist (no OS signals)
    stream = from_schema(real_app_schema).fuzz(FuzzConfig(max_steps=1))
    assert isinstance(next(stream), events.EngineStarted)
    # Advance into run_forever so the generator is suspended at a yield inside it
    next(stream)
    # Throw KeyboardInterrupt at the current yield suspension point
    interrupted = stream.generator.throw(KeyboardInterrupt)
    assert isinstance(interrupted, events.Interrupted)
    # Drain to EngineFinished
    for _ in stream:
        pass
