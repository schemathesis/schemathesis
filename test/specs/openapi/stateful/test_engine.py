import threading
from dataclasses import dataclass

import hypothesis
import hypothesis.errors
import pytest
from flask import Flask, jsonify, request

import schemathesis
from schemathesis.checks import not_a_server_error
from schemathesis.engine import Status, events
from schemathesis.engine.context import EngineContext
from schemathesis.engine.phases import Phase, PhaseName, stateful
from schemathesis.generation import GenerationMode
from schemathesis.specs.openapi.checks import (
    ignored_auth,
    response_schema_conformance,
    use_after_free,
)
from test.utils import flaky


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
    engine = engine_factory(
        app_kwargs={"independent_500": True}, checks=[not_a_server_error], max_failures=max_failures
    )
    result = collect_result(engine)
    assert result.events[-1].status == Status.FAILURE, result.errors
    if max_failures == 1:
        # Then only the first one should be found
        assert len(result.failures) == 1
    elif max_failures == 2:
        # Else, all of them should be found
        assert len(result.failures) == 2


def test_works_on_single_link(engine_factory):
    engine = engine_factory(app_kwargs={"single_link": True, "independent_500": True})
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


def test_stop_outside_of_state_machine_execution(engine_factory, mocker, stop_event):
    # When stop signal is received outside of state machine execution
    engine = engine_factory(
        app_kwargs={"independent_500": True},
    )
    mocker.patch(
        "schemathesis.engine.phases.stateful._executor.StatefulContext.mark_as_seen_in_run",
        side_effect=lambda *_, **__: stop_event.set(),
    )
    result = collect_result(engine)
    assert result.events[-1].status == Status.INTERRUPTED


@pytest.mark.parametrize(
    ["kwargs"],
    [({},), ({"unique_inputs": True},)],
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


@flaky(max_runs=10, min_passes=1)
@pytest.mark.usefixtures("restore_checks")
def test_failure_hidden_behind_another_failure(engine_factory):
    # The same API operation, but one error is far less frequent and is located behind another one that happens more often, so it is not found in the first test suite

    suite_number = 0

    @schemathesis.check
    def dynamic_check(*args, **kwargs):
        if suite_number == 0:
            not_a_server_error(*args, **kwargs)
        else:
            response_schema_conformance(*args, **kwargs)

    engine = engine_factory(
        app_kwargs={"failure_behind_failure": True},
        checks=[dynamic_check],
        max_examples=60,
    )
    failures = []
    for event in engine:
        if isinstance(event, events.SuiteFinished):
            suite_number += 1
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
    engine = engine_factory(app_kwargs={"multiple_conformance_issues": True})
    result = collect_result(engine)
    assert len(result.failures) == 2
    assert {check.failure_info.failure.title for check in result.failures} == {
        "Missing Content-Type header",
        "Response violates schema",
    }


@flaky(max_runs=10, min_passes=1)
def test_find_use_after_free(engine_factory):
    engine = engine_factory(
        app_kwargs={"use_after_free": True},
        checks=[use_after_free],
        max_examples=60,
    )
    result = collect_result(engine)
    assert len(result.failures) == 1
    assert result.failures[0].failure_info.failure.title == "Use after free"
    assert result.events[-1].status == Status.FAILURE


@pytest.mark.usefixtures("restore_checks")
def test_failed_health_check(engine_factory):
    @schemathesis.check
    def rejected_check(*args, **kwargs):
        hypothesis.reject()

    engine = engine_factory(
        hypothesis_settings={"suppress_health_check": [hypothesis.HealthCheck.differing_executors]},
        max_examples=1,
        checks=[rejected_check],
    )
    result = collect_result(engine)
    assert result.errors
    assert isinstance(result.errors[0].value, hypothesis.errors.FailedHealthCheck)
    assert result.events[-1].status == Status.ERROR


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


def test_unsatisfiable(engine_factory):
    engine = engine_factory(app_kwargs={"unsatisfiable": True}, max_examples=1)
    result = collect_result(engine)
    assert result.errors
    assert isinstance(result.errors[0].value, hypothesis.errors.InvalidArgument)
    assert result.events[-1].status == Status.ERROR


def test_custom_headers(engine_factory):
    headers = {"X-Foo": "Bar"}
    engine = engine_factory(app_kwargs={"custom_headers": headers}, max_examples=1, headers=headers)
    result = collect_result(engine)
    assert result.events[-1].status == Status.SUCCESS


def test_multiple_source_links(engine_factory):
    # When there are multiple links coming to the same operation from different operations
    # Then there should be no error during getting the previous step results
    engine = engine_factory(app_kwargs={"multiple_source_links": True}, max_examples=10)
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
        app_kwargs={"slowdown": 0.010},
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


def test_external_link(ctx, app_factory, app_runner):
    remote_app = app_factory(independent_500=True)
    remote_app_port = app_runner.run_flask_app(remote_app)
    base_ref = f"http://127.0.0.1:{remote_app_port}/openapi.json#/paths/~1users~1{{userId}}"
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
    delete_links = {
        "GetUser": {
            "operationId": "getUser",
            "parameters": {"userId": "$request.path.userId"},
        },
    }
    schema = ctx.openapi.build_schema(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/NewUser"}}},
                    },
                    "responses": {
                        "201": {
                            "description": "Successful response",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}},
                            "links": post_links,
                        },
                        "400": {"description": "Bad request"},
                        "default": {"description": "Default"},
                    },
                },
            },
            "/users/{userId}": {
                "parameters": [{"in": "path", "name": "userId", "required": True, "schema": {"type": "integer"}}],
                "get": {
                    "summary": "Get a user",
                    "operationId": "getUser",
                    "responses": {
                        "200": {
                            "description": "Successful response",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}},
                            "links": get_links,
                        },
                        "404": {"description": "User not found"},
                        "default": {"description": "Default"},
                    },
                },
                "delete": {
                    "summary": "Delete a user",
                    "operationId": "deleteUser",
                    "responses": {
                        "204": {
                            "description": "Successful response",
                            "links": delete_links,
                        },
                        "404": {"description": "User not found"},
                        "default": {"description": "Default"},
                    },
                },
                "patch": {
                    "summary": "Update a user",
                    "operationId": "updateUser",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/UpdateUser"}}},
                    },
                    "responses": {
                        "200": {
                            "description": "Successful response",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}},
                        },
                        "404": {"description": "User not found"},
                        "default": {"description": "Default"},
                    },
                },
            },
        },
        components={
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "last_modified": {"type": "string"},
                    },
                    "required": ["id", "name", "last_modified"],
                },
                "NewUser": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string", "maxLength": 50}},
                    "additionalProperties": False,
                },
            }
        },
    )
    root_app = app_factory(independent_500=True)
    root_app_port = app_runner.run_flask_app(root_app)
    schema = schemathesis.openapi.from_dict(schema)
    schema.config.update(base_url=f"http://127.0.0.1:{root_app_port}/")
    schema.config.generation.update(max_examples=75, database="none", modes=[GenerationMode.POSITIVE])
    engine = stateful.execute(
        engine=EngineContext(schema=schema, stop_event=threading.Event()),
        phase=Phase(name=PhaseName.STATEFUL_TESTING, is_supported=True, is_enabled=True),
    )
    result = collect_result(engine)
    assert result.events[-1].status == Status.FAILURE


def test_new_resource_is_not_available(engine_factory):
    # When a resource is not available after creation
    engine = engine_factory(
        app_kwargs={"ensure_resource_availability": True},
        max_examples=50,
    )
    result = collect_result(engine)
    # Then it is a failure
    assert result.events[-1].status == Status.FAILURE
    assert result.failures[0].failure_info.failure.title == "Resource is not available after creation"


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
        app_kwargs={"independent_500": True},
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

    app = Flask(__name__)

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
        phase=Phase(name=PhaseName.STATEFUL_TESTING, is_supported=True, is_enabled=True),
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

    app = Flask(__name__)

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
        phase=Phase(name=PhaseName.STATEFUL_TESTING, is_supported=True, is_enabled=True),
    )
    result = collect_result(engine)
    assert result.events[-1].status == Status.SUCCESS


def test_unique_inputs(engine_factory):
    engine = engine_factory(
        app_kwargs={"independent_500": True},
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


def test_ignored_auth_valid(engine_factory):
    # When auth works properly
    token = "Test"
    engine = engine_factory(
        app_kwargs={"auth_token": token},
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
        app_kwargs={"auth_token": token, "ignored_auth": True},
        checks=[ignored_auth],
        headers={"Authorization": "Bearer UNKNOWN"},
    )
    result = collect_result(engine)
    # Then it should be reported
    assert result.events[-1].status == Status.FAILURE
    assert result.failures[0].failure_info.failure.title == "API accepts requests without authentication"


def test_multiple_incoming_link_without_override(app_factory):
    app = app_factory(multiple_incoming_links_with_same_status=True)
    schema = schemathesis.openapi.from_dict(app.config["schema"])
    state_machine = schema.as_state_machine()
    assert (
        sum(len(operation.outgoing) for operation in state_machine._transitions.operations.values())
        == schema.statistic.links.total
    )


def test_circular_links(engine_factory):
    engine = engine_factory(app_kwargs={"circular_links": True}, max_examples=5)
    result = collect_result(engine)
    assert result.events[-1].status != Status.ERROR


def test_link_subset(engine_factory):
    engine = engine_factory(include={"method_regex": "POST|GET"}, max_examples=5)
    result = collect_result(engine)
    assert result.events[-1].status != Status.ERROR


def test_duplicate_operation_links(engine_factory):
    engine = engine_factory(app_kwargs={"duplicate_operation_links": True}, max_examples=5)
    result = collect_result(engine)
    assert result.events[-1].status != Status.ERROR


def test_list_users_as_root(engine_factory):
    engine = engine_factory(app_kwargs={"list_users_as_root": True}, max_examples=5)
    result = collect_result(engine)
    assert result.events[-1].status != Status.ERROR


def test_no_reliable_transitions(engine_factory):
    engine = engine_factory(app_kwargs={"no_reliable_transitions": True}, max_examples=5)
    result = collect_result(engine)
    assert result.events[-1].status != Status.ERROR
