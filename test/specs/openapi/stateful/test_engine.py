import threading
from dataclasses import dataclass

import hypothesis
import hypothesis.errors
import pytest
from flask import Flask, jsonify, request

import schemathesis
import schemathesis.openapi
from schemathesis.checks import not_a_server_error
from schemathesis.core.failures import FailureGroup
from schemathesis.core.transforms import deepclone
from schemathesis.engine import Status, events
from schemathesis.engine.context import EngineContext
from schemathesis.engine.core import ExecutionPlan
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import Phase, PhaseName, stateful
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.specs.openapi.checks import (
    ensure_resource_availability,
    ignored_auth,
    response_schema_conformance,
    use_after_free,
)
from test.apps.catalog.openapi.modifiers.stateful import (
    BearerAuth,
    CircularLinks,
    CustomHeadersCheck,
    DuplicateOperationLinks,
    EnsureResourceAvailability,
    FailureBehindFailure,
    IgnoredAuth,
    IndependentInternalError,
    ListUsersAsRoot,
    MultipleConformanceIssues,
    MultipleIncomingLinksWithSameStatus,
    MultipleSourceLinks,
    NoReliableTransitions,
    ReuseDeletedIds,
    ServedFromCache,
    SingleLink,
    Slowdown,
    Unsatisfiable,
    UseAfterFree,
)
from test.utils import check_context


@dataclass
class EngineResult:
    events: list[events.EngineEvent]

    @property
    def test_events(self):
        return [event for event in self.events if isinstance(event, events.TestEvent)]

    @property
    def event_names(self):
        return [event.__class__.__name__ for event in self.test_events]

    @property
    def failures(self):
        return [
            check
            for event in self.events
            if isinstance(event, events.ScenarioFinished)
            for checks in event.recorder.checks.values()
            for check in checks
            if check.status == Status.FAILURE
        ]

    @property
    def errors(self):
        return [event for event in self.events if isinstance(event, events.NonFatalError)]


def collect_result(events) -> EngineResult:
    return EngineResult(events=list(events))


@pytest.mark.parametrize("max_failures", [1, 2])
def test_find_independent_5xx(engine_factory, max_failures):
    # When the app contains multiple endpoints with 5xx responses
    engine = engine_factory(IndependentInternalError(), checks=[not_a_server_error], max_failures=max_failures)
    result = collect_result(engine)
    assert result.events[-1].status == Status.FAILURE, result.errors
    if max_failures == 1:
        # Then only the first one should be found
        assert len(result.failures) == 1
    elif max_failures == 2:
        # Else, all of them should be found
        assert len(result.failures) == 2


def test_works_on_single_link(engine_factory):
    engine = engine_factory(SingleLink(), IndependentInternalError())
    result = collect_result(engine)
    assert result.events[-1].status == Status.FAILURE, result.errors


def keyboard_interrupt(r):
    raise KeyboardInterrupt


def stop_engine(e):
    e.set()


@pytest.mark.parametrize("func", [keyboard_interrupt, stop_engine])
@pytest.mark.usefixtures("restore_checks")
def test_stop_in_check(engine_factory, func, stop_event):
    @schemathesis.check
    def stop_immediately(*args, **kwargs):
        func(stop_event)

    engine = engine_factory(checks=[stop_immediately])
    result = collect_result(engine)
    assert result.events[-1].status == Status.INTERRUPTED
    if func is keyboard_interrupt:
        scenario_finished = [ev for ev in result.events if isinstance(ev, events.ScenarioFinished)]
        assert len(scenario_finished) > 0
        assert scenario_finished[0].recorder.cases


@pytest.mark.parametrize("event_cls", [events.ScenarioStarted, events.ScenarioFinished])
def test_explicit_stop(engine_factory, event_cls, stop_event):
    engine = engine_factory()
    collected = []
    for event in engine:
        collected.append(event)
        if isinstance(event, event_cls):
            stop_event.set()
    assert len(collected) > 0
    assert collected[-1].status == Status.INTERRUPTED


@pytest.mark.usefixtures("restore_checks")
def test_stop_outside_of_state_machine_execution(engine_factory, stop_event):
    # The stop arrives with a failure, so it is first seen between suites rather than inside a scenario.
    @schemathesis.check
    def stop_on_server_error(ctx, response, case):
        if response.status_code >= 500:
            stop_event.set()

    engine = engine_factory(IndependentInternalError(), checks=[not_a_server_error, stop_on_server_error])
    result = collect_result(engine)
    assert result.events[-1].status == Status.INTERRUPTED


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"unique_inputs": True}],
)
@pytest.mark.usefixtures("restore_checks")
def test_internal_error_in_check(engine_factory, kwargs):
    @schemathesis.check
    def bugged_check(*args, **kwargs):
        raise ZeroDivisionError("Oops!")

    engine = engine_factory(**kwargs)
    result = collect_result(engine)
    assert result.errors
    assert isinstance(result.errors[0].value, ZeroDivisionError)


@pytest.mark.usefixtures("restore_checks")
def test_assumption_in_check_skips_step(engine_factory):
    @schemathesis.check
    def skip_reads(ctx, response, case):
        hypothesis.assume(case.method != "GET")

    result = collect_result(engine_factory(checks=[skip_reads], max_examples=10))
    assert (result.events[-1].status, result.errors, result.failures) == (Status.SUCCESS, [], [])


@pytest.mark.parametrize("exception_args", [(), ("Oops!",)])
@pytest.mark.usefixtures("restore_checks")
def test_custom_assertion_in_check(engine_factory, exception_args):
    @schemathesis.check
    def custom_check(*args, **kwargs):
        raise AssertionError(*exception_args)

    engine = engine_factory(checks=[custom_check], max_examples=1)
    result = collect_result(engine)
    # Failures on different API operations
    assert len(result.failures) <= 5
    failure = result.failures[0]
    assert failure.failure_info.failure.title == "Custom check failed: `custom_check`"
    if not exception_args:
        assert failure.failure_info.failure.message == ""
    else:
        assert failure.failure_info.failure.message == "Oops!"


@pytest.mark.usefixtures("restore_checks")
def test_custom_assertion_with_random_message(engine_factory):
    counter = 0

    @schemathesis.check
    def custom_check(*args, **kwargs):
        nonlocal counter
        counter += 1
        raise AssertionError(f"Fail counter: {counter}")

    engine = engine_factory(checks=[custom_check], max_examples=1)
    result = collect_result(engine)
    # Failures on different API operations
    assert len(result.failures) <= 5
    failure = result.failures[0]
    assert failure.failure_info.failure.title == "Custom check failed: `custom_check`"


@pytest.mark.usefixtures("restore_checks")
def test_distinct_assertions(engine_factory):
    counter = 0

    # When a check contains different failing assertions
    @schemathesis.check
    def custom_check(ctx, response, case):
        nonlocal counter
        counter += 1
        if counter == 1:
            raise AssertionError("First")
        if counter == 2:
            raise AssertionError("Second")
        if counter == 3:
            # No message
            assert case.headers == 43
        elif counter == 4:
            # With message
            assert case.headers == 43, "Fourth"

    engine = engine_factory(checks=[custom_check], max_examples=1)
    result = collect_result(engine)
    # Then all of them should be reported
    assert len(result.failures) == 4
    messages = {check.failure_info.failure.message for check in result.failures}
    assert "First" in messages
    assert "Second" in messages


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"max_failures": 1}],
)
@pytest.mark.usefixtures("restore_checks")
def test_flaky_assertions(engine_factory, kwargs):
    counter = 0

    # When a check contains different failing assertions and one of them is considered flaky by Hypothesis
    @schemathesis.check
    def custom_check(ctx, response, case):
        nonlocal counter
        counter += 1
        if counter == 1:
            raise AssertionError("First")
        if counter == 2:
            raise AssertionError("Second")
        assert case.headers == 43

    engine = engine_factory(checks=[custom_check], max_examples=1, **kwargs)
    result = collect_result(engine)
    # Then all of them should be reported
    if "max_failures" in kwargs:
        assert len(result.failures) == 1
        assert {check.failure_info.failure.message for check in result.failures} == {"First"}
    else:
        # Assertions happen on multiple API operations (3 on the same)
        assert len(result.failures) <= 7
        messages = {check.failure_info.failure.message for check in result.failures}
        assert "First" in messages
        assert "Second" in messages


@pytest.mark.usefixtures("restore_checks")
def test_failure_hidden_behind_another_failure(engine_factory):
    # The same API operation fails in two ways, and the second one is checked only after the first is found,
    # so it surfaces in a later test suite

    first_failure_found = False

    @schemathesis.check
    def dynamic_check(*args, **kwargs):
        if first_failure_found:
            response_schema_conformance(*args, **kwargs)
        else:
            not_a_server_error(*args, **kwargs)

    engine = engine_factory(
        FailureBehindFailure(),
        checks=[dynamic_check],
        # The bug needs a specific call sequence, which takes many scenarios to reach.
        max_examples=60,
    )
    failures = []
    for event in engine:
        # A suite may also restart without a failure, e.g. when a replayed scenario draws different data
        if isinstance(event, events.SuiteFinished) and failures:
            first_failure_found = True
        if isinstance(event, events.ScenarioFinished):
            failures.extend(
                [
                    check
                    for checks in event.recorder.checks.values()
                    for check in checks
                    if check.status == Status.FAILURE
                ]
            )
    assert len(failures) == 2
    assert {check.failure_info.failure.title for check in failures} == {"Response violates schema", "Server error"}


def test_multiple_conformance_issues(engine_factory):
    engine = engine_factory(MultipleConformanceIssues())
    result = collect_result(engine)
    assert len(result.failures) == 2
    assert {check.failure_info.failure.title for check in result.failures} == {
        "Missing Content-Type header",
        "Response violates schema",
    }


@pytest.mark.flaky(reruns=9)
def test_find_use_after_free(engine_factory):
    engine = engine_factory(
        UseAfterFree(),
        checks=[use_after_free],
        # The bug needs a specific call sequence, which takes many scenarios to reach.
        max_examples=60,
    )
    result = collect_result(engine)
    assert len(result.failures) == 1
    assert result.failures[0].failure_info.failure.title == "Deleted resource still available"
    assert result.events[-1].status == Status.FAILURE


_STATE_MACHINE_SETTINGS = hypothesis.settings(
    # Planted bugs need specific call sequences, which take many scenarios to reach.
    max_examples=60,
    stateful_step_count=10,
    deadline=None,
    suppress_health_check=list(hypothesis.HealthCheck),
)


def _use_after_free_state_machine(ctx):
    api = ctx.openapi.apps.stateful_users(UseAfterFree())
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["use_after_free"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])
    store = api.wsgi_app.config["store"]

    class StateMachine(schema.as_state_machine()):
        def setup(self):
            # Which bundle a step feeds depends on the status the server answers with, so a scenario
            # replayed while shrinking against a server the earlier scenarios have moved on can enable
            # a different set of rules, and Hypothesis reports that as inconsistent data generation.
            store.reset()

    return StateMachine


@pytest.mark.flaky(reruns=9)
def test_find_use_after_free_via_state_machine(ctx):
    with pytest.raises(FailureGroup) as exc_info:
        _use_after_free_state_machine(ctx).run(settings=_STATE_MACHINE_SETTINGS)
    assert any("Deleted resource still available" in str(e) for e in exc_info.value.exceptions)


@pytest.mark.flaky(reruns=9)
def test_find_use_after_free_via_state_machine_with_overridden_validate_response(ctx):
    class CustomStateMachine(_use_after_free_state_machine(ctx)):
        def validate_response(self, response, case, additional_checks=None, **kwargs):
            super().validate_response(response, case, additional_checks=additional_checks, **kwargs)

    with pytest.raises(FailureGroup) as exc_info:
        CustomStateMachine.run(settings=_STATE_MACHINE_SETTINGS)
    assert any("Deleted resource still available" in str(e) for e in exc_info.value.exceptions)


def test_no_false_positive_use_after_free_when_id_reused(engine_factory):
    # When a server reuses resource IDs after deletion, the stateful engine can produce a scenario tree like:
    #
    #   POST /users -> id=1 (root)
    #   └── DELETE /users/1 -> 204
    #       └── POST /users -> id=1 again  (circular link: DELETE -> POST)
    #           └── GET /users/1 -> 200   <-- current case being checked
    #
    # The resource was re-created with the same id between the DELETE and the GET; this is NOT a use-after-free.
    engine = engine_factory(
        CircularLinks(),
        ReuseDeletedIds(),
        checks=[use_after_free],
        # A false positive needs a specific call sequence, which takes many scenarios to reach.
        max_examples=60,
    )
    result = collect_result(engine)
    assert len(result.failures) == 0
    assert result.events[-1].status == Status.SUCCESS


def test_no_false_positive_ensure_resource_availability_when_id_reused(engine_factory):
    # When a server reuses resource IDs after deletion, the stateful engine can produce a scenario tree like:
    #
    #   POST /users -> id=1 (root)
    #   DELETE /users/1 -> 204
    #   POST /users -> id=1 again  (circular link: DELETE -> POST)
    #   DELETE /users/1 -> 204 (resource deleted again)
    #   GET /users/1 -> 404  <-- current case being checked
    #
    # The resource was re-created and then re-deleted; this is NOT a resource-not-available issue.
    engine = engine_factory(
        CircularLinks(),
        ReuseDeletedIds(),
        checks=[ensure_resource_availability],
        # A false positive needs a specific call sequence, which takes many scenarios to reach.
        max_examples=60,
    )
    result = collect_result(engine)
    assert len(result.failures) == 0
    assert result.events[-1].status == Status.SUCCESS


@pytest.mark.parametrize(
    "kwargs",
    [{"max_failures": None}, {"max_failures": 1}],
)
@pytest.mark.usefixtures("restore_checks")
def test_flaky(engine_factory, kwargs):
    found = False

    @schemathesis.check
    def flaky_check(*args, **kwargs):
        nonlocal found
        if not found:
            found = True
            raise AssertionError("Flaky")

    engine = engine_factory(checks=[flaky_check], max_examples=1, **kwargs)
    result = collect_result(engine)
    failures = result.failures
    assert len(failures) == 1
    assert failures[0].failure_info.failure.message == "Flaky"


def test_flaky_suite_retries_only_after_new_failure(ctx, stop_event):
    config = schemathesis.Config.from_dict(
        {
            "seed": 6,
            "checks": {"enabled": False, "positive_data_acceptance": {"enabled": True}},
            "generation": {"mode": "positive", "max-examples": 25, "database": "none"},
        }
    )
    schema = schemathesis.openapi.from_wsgi(
        "/openapi.json", ctx.openapi.apps.flaky_branch_blocks().wsgi_app, config=config
    )
    result = collect_result(
        stateful.execute(
            engine=EngineContext(schema=schema, stop_event=stop_event),
            phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
        )
    )

    assert len([event for event in result.events if isinstance(event, events.SuiteStarted)]) == 2


def test_unsatisfiable(engine_factory):
    engine = engine_factory(Unsatisfiable(), max_examples=1)
    result = collect_result(engine)
    assert result.errors
    assert isinstance(result.errors[0].value, hypothesis.errors.Unsatisfiable)
    assert "Cannot generate test data for request body (application/json)" in str(result.errors[0].value)
    assert result.events[-1].status == Status.ERROR


def test_custom_headers(engine_factory):
    headers = {"X-Foo": "Bar"}
    engine = engine_factory(CustomHeadersCheck(expected=headers), max_examples=1, headers=headers)
    result = collect_result(engine)
    assert result.events[-1].status == Status.SUCCESS


def test_multiple_source_links(engine_factory):
    # When there are multiple links coming to the same operation from different operations
    # Then there should be no error during getting the previous step results
    engine = engine_factory(MultipleSourceLinks(), max_examples=10)
    result = collect_result(engine)
    assert not result.errors, result.errors


def test_max_response_time_valid(engine_factory):
    engine = engine_factory(
        max_examples=1,
        checks=[],
        max_response_time=10.0,
    )
    result = collect_result(engine)
    assert not result.errors, result.errors
    assert list(result.test_events[-2].recorder.checks.values())[0][0].name == "max_response_time"


def test_max_response_time_invalid(engine_factory):
    engine = engine_factory(
        Slowdown(seconds=0.010),
        max_steps=2,
        max_examples=1,
        checks=[],
        max_response_time=0.005,
    )
    result = collect_result(engine)
    failures = result.failures
    assert failures[0].failure_info.failure.message.startswith("Actual")
    assert failures[0].failure_info.failure.message.endswith("Limit: 5.00ms")


def test_targeted(engine_factory):
    calls = 0

    def custom_target(ctx):
        nonlocal calls
        calls += 1
        return 1.0

    engine = engine_factory(
        max_steps=5,
        max_examples=1,
        checks=[not_a_server_error],
        maximize=[custom_target],
    )
    result = collect_result(engine)
    assert not result.errors, result.errors
    assert calls > 0


def test_external_link(ctx):
    remote_api = ctx.openapi.apps.stateful_users(IndependentInternalError())
    base_ref = f"{remote_api.schema_url}#/paths/~1users~1{{userId}}"
    post_links = {
        "GetUser": {
            "operationRef": f"{base_ref}/get",
            "parameters": {"userId": "$response.body#/id"},
        },
        "DeleteUser": {
            "operationRef": f"{base_ref}/delete",
            "parameters": {"userId": "$response.body#/id"},
        },
        "UpdateUser": {
            "operationRef": f"{base_ref}/patch",
            "parameters": {"userId": "$response.body#/id"},
            "requestBody": {
                "last_modified": "$response.body#/last_modified",
            },
        },
    }
    get_links = {
        "DeleteUser": {
            "operationId": "deleteUser",
            "parameters": {"userId": "$request.path.userId"},
        },
    }
    # 75 examples reach the bug only in this scenario space, so drop what the links never use.
    raw_schema = deepclone(remote_api.spec)
    del raw_schema["paths"]["/users"]["get"]
    del raw_schema["paths"]["/orders/{orderId}"]
    del raw_schema["components"]["schemas"]["UpdateUser"]
    raw_schema["paths"]["/users"]["post"]["responses"]["201"]["links"] = post_links
    raw_schema["paths"]["/users/{userId}"]["get"]["responses"]["200"]["links"] = get_links
    schema = ctx.openapi.from_full_schema(raw_schema)
    root_api = ctx.openapi.apps.stateful_users(IndependentInternalError())
    schema.config.update(base_url=f"{root_api.base_url}/")
    # The bug needs a specific call sequence, which takes many scenarios to reach.
    schema.config.generation.update(max_examples=75, database="none", modes=[GenerationMode.POSITIVE])
    engine = stateful.execute(
        engine=EngineContext(schema=schema, stop_event=threading.Event()),
        phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
    )
    result = collect_result(engine)
    assert result.events[-1].status == Status.FAILURE


def test_new_resource_is_not_available(engine_factory):
    # When a resource is not available after creation
    engine = engine_factory(
        EnsureResourceAvailability(),
        max_examples=50,
    )
    result = collect_result(engine)
    # Then it is a failure
    assert result.events[-1].status == Status.FAILURE
    assert result.failures[0].failure_info.failure.title == "Created resource not found"


def test_new_resource_is_not_available_behind_cache(engine_factory):
    engine = engine_factory(EnsureResourceAvailability(), ServedFromCache(), max_examples=50)
    result = collect_result(engine)
    assert result.failures[0].failure_info.failure.message.endswith(
        "The response came from a cache (`Age: 120`) and may be stale"
    )


def test_resource_availability(engine_factory):
    # By default it is available unless was explicitly deleted
    # Ensure the check properly finds such DELETE calls
    engine = engine_factory(max_examples=50)
    result = collect_result(engine)
    event = result.events[-1]
    if event.status != Status.SUCCESS:
        pytest.fail(str(result.events))


def test_negative_tests(engine_factory):
    engine = engine_factory(
        IndependentInternalError(),
        max_examples=50,
        generation_modes=list(GenerationMode),
    )
    result = collect_result(engine)
    event = result.events[-1]
    if event.status != Status.FAILURE:
        pytest.fail(str(result.events))


def test_negative_changing_to_positive(app_runner):
    # See GH-2983
    schema = {
        "openapi": "3.1.0",
        "paths": {
            "/Customers/{id}": {
                "put": {
                    "operationId": "UpdateCustomer",
                    "parameters": [
                        {"name": "id", "in": "path", "schema": {"type": "integer"}},
                    ],
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CustomerUpdate"}}}
                    },
                    "responses": {"default": {"description": "Ok"}},
                }
            },
            "/Customers": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CustomerCreate"},
                            }
                        }
                    },
                    "responses": {
                        "201": {
                            "links": {
                                "Customer": {"$ref": "#/components/links/updateCustomer"},
                            }
                        }
                    },
                }
            },
        },
        "components": {
            "links": {
                "updateCustomer": {
                    "operationId": "UpdateCustomer",
                    "parameters": {"id": "$response.body"},
                    "requestBody": {"name": "firstname"},
                }
            },
            "schemas": {
                "CustomerUpdate": {
                    "required": ["name"],
                    "additionalProperties": False,
                    "properties": {"name": {"type": "string"}},
                },
                "CustomerCreate": {"type": "object", "properties": {"name": {"type": "string"}}},
            },
        },
    }

    app = Flask("schemathesis_test")

    CUSTOMERS = {}
    NEXT_ID = 1

    @app.route("/Customers/<int:id>", methods=["PUT"])
    def update_customer(id):
        if id not in CUSTOMERS:
            return "", 404
        data = request.get_json(force=True, silent=True)
        if not data or not isinstance(data, dict) or list(data) != ["name"] or not isinstance(data["name"], str):
            return jsonify({"error": ["Invalid JSON"]}), 400
        CUSTOMERS[id].update(data)
        return "", 204

    @app.route("/Customers", methods=["POST"])
    def create_customer():
        nonlocal NEXT_ID
        data = request.get_json(force=True, silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": ["Invalid JSON"]}), 400

        name = data.get("name")
        if not (isinstance(name, str) and 1 <= len(name) <= 3):
            return jsonify({"error": ["Invalid name. Must be 1 to 3 characters."]}), 400

        customer_id = NEXT_ID
        NEXT_ID += 1
        data["id"] = customer_id
        CUSTOMERS[customer_id] = data
        return jsonify(customer_id), 201

    app_port = app_runner.run_flask_app(app)

    config = schemathesis.Config.from_dict(
        {
            "checks": {
                "enabled": False,
                "negative_data_rejection": {"enabled": True},
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema, config=config)
    schema.config.update(base_url=f"http://127.0.0.1:{app_port}/")
    schema.config.generation.update(database="none")
    engine = stateful.execute(
        engine=EngineContext(schema=schema, stop_event=threading.Event()),
        phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
    )
    result = collect_result(engine)
    assert result.events[-1].status == Status.SUCCESS


def test_explicit_auth_header_does_not_trigger_negative_data_rejection(app_runner):
    schema = {
        "openapi": "3.1.0",
        "paths": {
            "/organizations/": {
                "post": {
                    "responses": {"201": {"links": {"get": {"operationId": "organizations:get"}}}},
                    "security": [{"HTTPBearer": []}],
                },
                "get": {"operationId": "organizations:get", "responses": {}},
            }
        },
        "components": {"securitySchemes": {"HTTPBearer": {"type": "http"}}},
    }

    app = Flask("schemathesis_test")

    def check_auth():
        auth_header = request.headers.get("Authorization")
        return auth_header and auth_header.startswith("Bearer ")

    def auth_error():
        return jsonify({"detail": "Not authenticated"}), 401

    @app.route("/organizations/", methods=["GET"])
    def organizations_list():
        if not check_auth():
            return auth_error()

        return jsonify([])

    @app.route("/organizations/", methods=["POST"])
    def organizations_create():
        if not check_auth():
            return auth_error()
        return jsonify({})

    app_port = app_runner.run_flask_app(app)

    config = schemathesis.Config.from_dict(
        {
            "base-url": f"http://127.0.0.1:{app_port}/",
            "headers": {"Authorization": "Bearer secret"},
            "checks": {
                "enabled": False,
                "negative_data_rejection": {"enabled": True},
            },
            "generation": {
                "max-examples": 10,
            },
        }
    )
    schema = schemathesis.openapi.from_dict(schema, config=config)
    engine = stateful.execute(
        engine=EngineContext(schema=schema, stop_event=threading.Event()),
        phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
    )
    result = collect_result(engine)
    assert result.events[-1].status == Status.SUCCESS


def test_unique_inputs(engine_factory):
    engine = engine_factory(
        IndependentInternalError(),
        unique_inputs=True,
        max_steps=50,
        max_examples=25,
    )
    cases = []
    for event in engine:
        if isinstance(event, events.ScenarioStarted):
            cases.clear()
        elif isinstance(event, events.ScenarioFinished):
            assert len(cases) == len(set(cases)), "Duplicate cases found"


def test_explicit_hypothesis_default_max_steps(ctx):
    api = ctx.openapi.apps.stateful_users()
    # Hypothesis may end a scenario early at random, so the run is seeded.
    config = schemathesis.Config.from_dict(
        {
            "seed": 0,
            "phases": {"stateful": {"max-steps": 50}},
            "generation": {"max-examples": 1, "database": "none"},
            "checks": {"enabled": False},
        }
    )
    schema = schemathesis.openapi.from_url(api.schema_url, config=config)
    engine = stateful.execute(
        engine=EngineContext(schema=schema, stop_event=threading.Event()),
        phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
    )

    assert [len(event.recorder.cases) for event in engine if isinstance(event, events.ScenarioFinished)][-1] == 50


def test_ignored_auth_valid(engine_factory):
    # When auth works properly
    token = "Test"
    engine = engine_factory(
        BearerAuth(token=token),
        checks=[ignored_auth],
        headers={"Authorization": f"Bearer {token}"},
    )
    result = collect_result(engine)
    # Then no failures are reported
    event = result.events[-1]
    if event.status != Status.SUCCESS:
        pytest.fail(str(result.events))


def test_ignored_auth_invalid(engine_factory):
    # When auth is ignored
    token = "Test"
    engine = engine_factory(
        IgnoredAuth(token=token),
        checks=[ignored_auth],
        headers={"Authorization": "Bearer UNKNOWN"},
    )
    result = collect_result(engine)
    # Then it should be reported
    assert result.events[-1].status == Status.FAILURE
    assert result.failures[0].failure_info.failure.title == "API accepts requests without authentication"


def test_multiple_incoming_link_without_override(ctx):
    api = ctx.openapi.apps.stateful_users(MultipleIncomingLinksWithSameStatus())
    schema = schemathesis.openapi.from_dict(api.spec)
    state_machine = schema.as_state_machine()
    assert (
        sum(len(operation.outgoing) for operation in state_machine._transitions.operations.values())
        == schema.statistic.transitions.total
    )


def test_transition_count_follows_chained_response_references(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {"post": {"responses": {"201": {"$ref": "#/components/responses/Alias"}}}},
            "/users/{id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        components={
            "responses": {
                "Alias": {"$ref": "#/components/responses/Created"},
                "Created": {
                    "description": "Created",
                    "links": {"Get": {"operationId": "getUser", "parameters": {"id": "$response.body#/id"}}},
                },
            },
        },
    )
    assert (schema.statistic.transitions.total, schema.statistic.transitions.selected) == (1, 1)


def test_transition_count_with_non_object_links(ctx):
    schema = ctx.openapi.load_schema(
        {"/users": {"get": {"responses": {"200": {"description": "OK", "links": {"A": "note", "B": [1]}}}}}}
    )
    assert (schema.statistic.transitions.total, schema.statistic.transitions.selected) == (2, 0)


SHARED_RESPONSE_SCHEMA = {
    "openapi": "3.0.2",
    "info": {"title": "Test", "version": "1.0"},
    "paths": {
        "/users": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}}
                    },
                },
                "responses": {"201": {"$ref": "#/components/responses/User"}},
            }
        },
        "/admins": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}}
                    },
                },
                "responses": {"201": {"$ref": "#/components/responses/User"}},
            }
        },
        "/users/{userId}": {
            "get": {
                "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK"}},
            }
        },
    },
    "components": {
        "responses": {
            "User": {
                "description": "Created",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"userId": {"type": "string"}, "name": {"type": "string"}},
                            "required": ["userId"],
                        }
                    }
                },
            }
        }
    },
}


# Operations sharing one response definition each get their own transition, so the reported total must not collapse.
def test_reported_transition_count_with_shared_response_definition(ctx):
    schema = ctx.openapi.from_full_schema(SHARED_RESPONSE_SCHEMA)
    # Mirrors the CLI, which reads the load-time statistic before inference runs
    assert schema.statistic.transitions.total == 0
    engine = EngineContext(schema=schema, stop_event=threading.Event())
    phase = Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=False, skip_reason=None)
    payload = ExecutionPlan(phases=[phase])._adapt_execution(engine, phase)
    state_machine = schema.as_state_machine()
    assert payload.transitions_total == sum(
        len(operation.outgoing) for operation in state_machine._transitions.operations.values()
    )


def test_circular_links(engine_factory):
    engine = engine_factory(CircularLinks(), max_examples=5)
    result = collect_result(engine)
    assert result.events[-1].status != Status.ERROR


def test_link_subset(engine_factory):
    engine = engine_factory(include={"method_regex": "POST|GET"}, max_examples=5)
    result = collect_result(engine)
    assert result.events[-1].status != Status.ERROR


def test_duplicate_operation_links(engine_factory):
    engine = engine_factory(DuplicateOperationLinks(), max_examples=5)
    result = collect_result(engine)
    assert result.events[-1].status != Status.ERROR


def test_list_users_as_root(engine_factory):
    engine = engine_factory(ListUsersAsRoot(), max_examples=5)
    result = collect_result(engine)
    assert result.events[-1].status != Status.ERROR


def test_no_reliable_transitions(engine_factory):
    engine = engine_factory(NoReliableTransitions(), max_examples=5)
    result = collect_result(engine)
    assert result.events[-1].status != Status.ERROR


def test_no_false_positive_ensure_resource_availability_cross_subtree(ctx, response_factory):
    # When a DELETE in one root's subtree deletes a resource that was re-created by a different
    # root, ensure_resource_availability must not be triggered for operations from that second root.
    #
    # Recorder state:
    #   Root A  POST /users -> 201         [parent=None]
    #     DELETE /users/1 -> 204           [parent=A]   <- in A's subtree
    #   Root B  POST /users -> 201         [parent=None, separate root, same id reused]
    #     GET /users/1 -> 404              [parent=B]   <- 404 because the DELETE above ran after B created id=1
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            },
            "/users/{userId}": {
                "parameters": [{"in": "path", "name": "userId", "required": True, "schema": {"type": "integer"}}],
                "get": {
                    "operationId": "getUser",
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not Found"}},
                },
                "delete": {
                    "operationId": "deleteUser",
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
        }
    )

    post_op = schema["/users"]["POST"]
    get_op = schema["/users/{userId}"]["GET"]
    delete_op = schema["/users/{userId}"]["DELETE"]

    def make_http_response(method, url, status_code):
        return response_factory.core(
            status_code=status_code, content=b"", content_type=None, method=method, url=url, elapsed=0.0
        )

    recorder = ScenarioRecorder(label="test")

    root_a = Case(post_op, "POST", "/users")
    recorder.record_case(parent_id=None, case=root_a, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=root_a.id, response=make_http_response("POST", "http://localhost/users", 201))

    delete_a = Case(delete_op, "DELETE", "/users/{userId}", path_parameters={"userId": 1})
    recorder.record_case(parent_id=root_a.id, case=delete_a, transition=None, is_transition_applied=False)
    recorder.record_response(
        case_id=delete_a.id, response=make_http_response("DELETE", "http://localhost/users/1", 204)
    )

    root_b = Case(post_op, "POST", "/users")
    recorder.record_case(parent_id=None, case=root_b, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=root_b.id, response=make_http_response("POST", "http://localhost/users", 201))

    # GET /users/1 from root B — returns 404 because the DELETE in A's subtree ran after B was created
    get_b = Case(get_op, "GET", "/users/{userId}", path_parameters={"userId": 1})
    recorder.record_case(parent_id=root_b.id, case=get_b, transition=None, is_transition_applied=False)
    response_404 = make_http_response("GET", "http://localhost/users/1", 404)
    recorder.record_response(case_id=get_b.id, response=response_404)

    check_ctx = check_context(recorder=recorder)

    # Should not raise — the DELETE that explains the 404 is in A's subtree.
    assert ensure_resource_availability(check_ctx, response_404, get_b) is None


def test_reproduce_chain_renders_each_step_with_its_own_headers(ctx, app_runner, stop_event):
    # A bodyless step must not be printed with the failing step's `Content-Type`.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                            "links": {
                                "create": {
                                    "operationId": "createItem",
                                    "parameters": {"itemId": "$response.body#/itemId"},
                                }
                            },
                        }
                    },
                }
            },
            "/items/{itemId}": {
                "post": {
                    "operationId": "createItem",
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    @app.route("/items", methods=["GET"])
    def list_items():
        return jsonify({"itemId": "linked"})

    @app.route("/items/<item_id>", methods=["POST"])
    def create_item(item_id):
        if item_id == "linked":
            return jsonify({"detail": "boom"}), 500
        return jsonify({}), 200

    port = app_runner.run_flask_app(app)
    config = schemathesis.Config.from_dict(
        {
            "max-failures": 1,
            "checks": {"enabled": False, "not_a_server_error": {"enabled": True}},
            "generation": {"mode": "positive", "max-examples": 10, "database": "none"},
            "phases": {"stateful": {"inference": {"algorithms": []}}},
        }
    )
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json", config=config)
    result = collect_result(
        stateful.execute(
            engine=EngineContext(schema=schema, stop_event=stop_event),
            phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
        )
    )

    [failure] = result.failures
    commands = failure.failure_info.code_sample.splitlines()
    assert {command for command in commands if command.startswith("curl -X GET")} == {
        f"curl -X GET http://127.0.0.1:{port}/items"
    }
    assert all(
        command.startswith("curl -X POST -H 'Content-Type: multipart/form-data; boundary=")
        for command in commands
        if command.startswith("curl -X POST")
    )


# A POST to the resource's own URI created it only if it says so with `201` or a `Location` header.
@pytest.mark.parametrize(
    ("status_code", "with_location", "expected"),
    [
        (200, False, []),
        (200, True, ["Created resource not found"]),
        (201, False, ["Created resource not found"]),
    ],
    ids=["no-creation-evidence", "location-header", "created-status"],
)
def test_ensure_resource_availability_after_own_path_post(
    ctx, app_runner, stop_event, status_code, with_location, expected
):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users/{userId}": {
                "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "post": {
                    "operationId": "removeUser",
                    "responses": {
                        str(status_code): {
                            "description": "Removed",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"userId": {"type": "integer"}},
                                    }
                                }
                            },
                        }
                    },
                },
                "get": {
                    "operationId": "getUser",
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not Found"}},
                },
            },
        }
    )

    @app.route("/users/<int:user_id>", methods=["POST"])
    def remove_user(user_id):
        headers = {"Location": f"/users/{user_id}"} if with_location else {}
        return jsonify({"userId": user_id}), status_code, headers

    @app.route("/users/<int:user_id>", methods=["GET"])
    def get_user(user_id):
        return jsonify({"detail": "not found"}), 404

    port = app_runner.run_flask_app(app)
    config = schemathesis.Config.from_dict(
        {
            "max-failures": 1,
            "checks": {"enabled": False, "ensure_resource_availability": {"enabled": True}},
            "generation": {"mode": "positive", "max-examples": 40, "database": "none"},
        }
    )
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json", config=config)
    result = collect_result(
        stateful.execute(
            engine=EngineContext(schema=schema, stop_event=stop_event),
            phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
        )
    )

    assert [failure.failure_info.failure.title for failure in result.failures] == expected


# A resource removed by a POST is as gone as one removed by DELETE; the follow-up 404 explains itself.
def test_removal_by_non_delete_verb_suppresses_resource_availability(ctx, app_runner, stop_event):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"userId": {"type": "integer"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/users/{userId}": {
                "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "post": {
                    "operationId": "removeUser",
                    "responses": {"200": {"description": "Removed"}},
                },
                "get": {
                    "operationId": "getUser",
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not Found"}},
                },
            },
        }
    )

    users = {}
    counter = {"next": 1}

    @app.route("/users", methods=["POST"])
    def create_user():
        user_id = counter["next"]
        counter["next"] += 1
        users[user_id] = "u"
        return jsonify({"userId": user_id}), 201

    @app.route("/users/<int:user_id>", methods=["POST"])
    def remove_user(user_id):
        users.pop(user_id, None)
        return jsonify({"removed": user_id}), 200

    @app.route("/users/<int:user_id>", methods=["GET"])
    def get_user(user_id):
        if user_id not in users:
            return jsonify({"detail": "not found"}), 404
        return jsonify({"userId": user_id}), 200

    port = app_runner.run_flask_app(app)
    config = schemathesis.Config.from_dict(
        {
            "max-failures": 1,
            "checks": {"enabled": False, "ensure_resource_availability": {"enabled": True}},
            "generation": {"mode": "positive", "max-examples": 40, "database": "none"},
        }
    )
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json", config=config)
    result = collect_result(
        stateful.execute(
            engine=EngineContext(schema=schema, stop_event=stop_event),
            phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
        )
    )

    assert result.failures == [], [failure.failure_info.failure.title for failure in result.failures]


@pytest.mark.parametrize("media_type", ["application/json", "application/x-www-form-urlencoded"])
def test_inferred_body_link_sends_a_listed_value(ctx, app_runner, stop_event, media_type):
    language = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "code": {"type": "string"}},
        "required": ["name", "code"],
    }
    check = {"type": "object", "properties": {"language": {"type": "string"}}, "required": ["language"]}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/languages": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"type": "array", "items": language}}},
                        }
                    }
                }
            },
            "/check": {
                "post": {
                    "requestBody": {"required": True, "content": {media_type: {"schema": check}}},
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    sent = []

    @app.route("/languages", methods=["GET"])
    def list_languages():
        return jsonify([{"name": "Planted", "code": "planted-code"}])

    @app.route("/check", methods=["POST"])
    def check_text():
        payload = request.get_json(silent=True) if request.is_json else request.form
        sent.append((payload or {}).get("language"))
        return jsonify({}), 200

    port = app_runner.run_flask_app(app)
    config = schemathesis.Config.from_dict(
        {"checks": {"enabled": False}, "generation": {"mode": "positive", "max-examples": 10, "database": "none"}}
    )
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json", config=config)
    collect_result(
        stateful.execute(
            engine=EngineContext(schema=schema, stop_event=stop_event),
            phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
        )
    )

    assert "planted-code" in sent


def test_inferred_form_body_link_sends_credentials_from_unique_producer(ctx, app_runner, stop_event):
    application = {
        "type": "object",
        "properties": {
            "client_id": {"type": "string"},
            "client_secret": {"type": "string"},
            "name": {"type": "string"},
            "msg": {"type": "string"},
        },
        "required": ["client_id", "client_secret", "name", "msg"],
    }
    token_request = {
        "type": "object",
        "properties": {
            "client_id": {"type": "string"},
            "client_secret": {"type": "string"},
            "grant_type": {"type": "string", "enum": ["client_credentials"]},
        },
        "required": ["client_id", "client_secret", "grant_type"],
    }
    components = {"schemas": {"OAuth2Application": application, "OAuth2TokenRequest": token_request}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/v1/auth_tokens/register/": {
                "post": {
                    "operationId": "register",
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/OAuth2Application"}}
                            },
                        }
                    },
                }
            },
            "/v1/auth_tokens/token/": {
                "post": {
                    "operationId": "token",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {"$ref": "#/components/schemas/OAuth2TokenRequest"}
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        components=components,
    )
    sent = []

    @app.route("/v1/auth_tokens/register/", methods=["POST"])
    def register_application():
        return jsonify(
            {
                "client_id": "registered-client",
                "client_secret": "registered-secret",
                "name": "Test application",
                "msg": "Created",
            }
        ), 201

    @app.route("/v1/auth_tokens/token/", methods=["POST"])
    def create_token():
        sent.append((request.form.get("client_id"), request.form.get("client_secret")))
        return jsonify({}), 200

    config = schemathesis.Config.from_dict(
        {"checks": {"enabled": False}, "generation": {"mode": "positive", "max-examples": 10, "database": "none"}}
    )
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app), config=config)
    collect_result(
        stateful.execute(
            engine=EngineContext(schema=schema, stop_event=stop_event),
            phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
        )
    )

    assert ("registered-client", "registered-secret") in sent


def _first_step_of_first_scenario(app_runner, app, stop_event, seed):
    config = schemathesis.Config.from_dict(
        {
            "seed": seed,
            "generation": {"mode": "positive", "max-examples": 1, "database": "none"},
            "phases": {"stateful": {"inference": {"algorithms": []}}},
        }
    )
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app), config=config)
    result = collect_result(
        stateful.execute(
            engine=EngineContext(schema=schema, stop_event=stop_event),
            phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
        )
    )
    first_scenario = next(
        event for event in result.events if isinstance(event, events.ScenarioFinished) and event.recorder.cases
    )
    return next(iter(first_scenario.recorder.cases.values())).value.operation.label


def test_suites_start_from_different_roots(ctx, app_runner, stop_event):
    # Every suite opens with its simplest scenario, which must not always start from the alphabetically first root.
    roots = ["alpha", "beta", "gamma", "delta"]
    link = {"item": {"operationId": "getItem", "parameters": {"itemId": "$response.body#/itemId"}}}
    app, _ = ctx.openapi.make_flask_app(
        {
            **{f"/{name}": {"get": {"responses": {"200": {"description": "OK", "links": link}}}} for name in roots},
            "/items/{itemId}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    @app.route("/<name>", methods=["GET"])
    def list_root(name):
        return jsonify({"itemId": name})

    @app.route("/items/<item_id>", methods=["GET"])
    def get_item(item_id):
        return jsonify({})

    first_steps = [_first_step_of_first_scenario(app_runner, app, stop_event, seed) for seed in range(12)]
    assert first_steps.count("GET /alpha") <= 6, first_steps
    assert len(set(first_steps)) >= 3, first_steps


ITEM_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
    "required": ["id", "name"],
}


def _listed_items_app(ctx):
    return ctx.openapi.make_flask_app(
        {
            "/announcements": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"type": "array", "items": ITEM_SCHEMA}}},
                            "links": {
                                "GetItem": {"operationId": "getItem", "parameters": {"id": "$response.body#/0/id"}}
                            },
                        },
                        "401": {"description": "Unauthorized"},
                    }
                }
            },
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {"application/json": {"schema": ITEM_SCHEMA}},
                            "links": {
                                "GetItem": {"operationId": "getItem", "parameters": {"id": "$response.body#/id"}}
                            },
                        }
                    },
                }
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                }
            },
        }
    )


def _run_items_app(app, app_runner, stop_event, *, max_examples):
    config = schemathesis.Config.from_dict(
        {
            "seed": 1,
            "checks": {"enabled": False},
            "generation": {"mode": "positive", "max-examples": max_examples, "database": "none"},
        }
    )
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app), config=config)
    return collect_result(
        stateful.execute(
            engine=EngineContext(schema=schema, stop_event=stop_event),
            phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
        )
    )


def _root_steps(result):
    return [
        [
            f"{node.value.method} {node.value.path}"
            for node in event.recorder.cases.values()
            if node.parent_id is None and node.transition is None
        ]
        for event in result.events
        if isinstance(event, events.ScenarioFinished)
    ]


@pytest.mark.parametrize(
    "unproductive_response",
    [({"detail": "Unauthorized"}, 401), ([], 200)],
    ids=["unauthorized", "empty-list"],
)
def test_root_that_feeds_no_link_is_not_repeated(ctx, app_runner, stop_event, unproductive_response):
    app, _ = _listed_items_app(ctx)
    items = {}

    @app.route("/announcements", methods=["GET"])
    def list_announcements():
        return jsonify(unproductive_response[0]), unproductive_response[1]

    @app.route("/items", methods=["POST"])
    def create_item():
        item_id = len(items) + 1
        items[item_id] = {"id": item_id, "name": "item"}
        return jsonify(items[item_id]), 201

    @app.route("/items/<int:item_id>", methods=["GET"])
    def get_item(item_id):
        if item_id not in items:
            return jsonify({"detail": "Not found"}), 404
        return jsonify(items[item_id]), 200

    scenarios = _root_steps(_run_items_app(app, app_runner, stop_event, max_examples=50))

    assert [steps for steps in scenarios if steps.count("GET /announcements") > 2] == []


def _run_items_app_with_server_errors(app, app_runner, stop_event):
    config = schemathesis.Config.from_dict(
        {
            "seed": 1,
            "checks": {"enabled": False, "not_a_server_error": {"enabled": True}},
            "generation": {"mode": "positive", "max-examples": 50, "database": "none"},
        }
    )
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app), config=config)
    return collect_result(
        stateful.execute(
            engine=EngineContext(schema=schema, stop_event=stop_event),
            phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
        )
    )


def test_server_error_on_root_that_feeds_no_link_is_reported(ctx, app_runner, stop_event):
    app, _ = _listed_items_app(ctx)

    @app.route("/announcements", methods=["GET"])
    def list_announcements():
        return jsonify({"detail": "Internal Server Error"}), 500

    @app.route("/items", methods=["POST"])
    def create_item():
        return jsonify({"id": 1, "name": "item"}), 201

    @app.route("/items/<int:item_id>", methods=["GET"])
    def get_item(item_id):
        return jsonify({"id": item_id, "name": "item"}), 200

    result = _run_items_app_with_server_errors(app, app_runner, stop_event)

    assert {failure.failure_info.failure.title for failure in result.failures} == {"Server error"}


def test_bug_behind_root_that_feeds_links_once_data_exists(ctx, app_runner, stop_event):
    # Listing is empty until an item is created; only the listing exposes item ids.
    app, _ = _listed_items_app(ctx)
    items = set()

    @app.route("/announcements", methods=["GET"])
    def list_announcements():
        return jsonify([{"id": item_id, "name": "item"} for item_id in sorted(items)]), 200

    @app.route("/items", methods=["POST"])
    def create_item():
        item_id = 10_000_000 + len(items)
        items.add(item_id)
        return jsonify({"id": 0, "name": "item"}), 201

    @app.route("/items/<int:item_id>", methods=["GET"])
    def get_item(item_id):
        if item_id in items:
            return jsonify({"detail": "Internal Server Error"}), 500
        return jsonify({"detail": "Not found"}), 404

    result = _run_items_app_with_server_errors(app, app_runner, stop_event)

    assert {failure.failure_info.failure.title for failure in result.failures} == {"Server error"}
