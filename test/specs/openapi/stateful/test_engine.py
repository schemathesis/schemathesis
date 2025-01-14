import threading
from dataclasses import dataclass

import hypothesis
import hypothesis.errors
import pytest

import schemathesis
from schemathesis.checks import CHECKS, ChecksConfig, max_response_time, not_a_server_error
from schemathesis.core.failures import MaxResponseTimeConfig
from schemathesis.engine import Status, events
from schemathesis.engine.config import EngineConfig, ExecutionConfig, NetworkConfig
from schemathesis.engine.context import EngineContext
from schemathesis.engine.phases import Phase, PhaseName, stateful
from schemathesis.generation import GenerationConfig, GenerationMode
from schemathesis.specs.openapi.checks import ignored_auth, response_schema_conformance, use_after_free
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

    @property
    def steps_before_first_failure(self):
        steps = 0
        for event in self.test_events:
            if isinstance(event, events.StepFinished):
                if event.status == Status.FAILURE:
                    break
                steps += 1
        return steps

    @property
    def responses(self):
        return [event.response for event in self.events if isinstance(event, events.StepFinished)]


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
def test_stop_in_check(engine_factory, func, stop_event):
    def stop_immediately(*args, **kwargs):
        func(stop_event)

    engine = engine_factory(checks=[stop_immediately])
    result = collect_result(engine)
    assert "StepStarted" in result.event_names
    assert "StepFinished" in result.event_names
    assert result.events[-1].status == Status.INTERRUPTED


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
    [({},), ({"unique_data": True},)],
)
def test_internal_error_in_check(engine_factory, kwargs):
    def bugged_check(*args, **kwargs):
        raise ZeroDivisionError("Oops!")

    engine = engine_factory(checks=[bugged_check], **kwargs)
    result = collect_result(engine)
    assert result.errors
    assert isinstance(result.errors[0].value, ZeroDivisionError)


@pytest.mark.parametrize("exception_args", [(), ("Oops!",)])
def test_custom_assertion_in_check(engine_factory, exception_args):
    def custom_check(*args, **kwargs):
        raise AssertionError(*exception_args)

    engine = engine_factory(
        checks=[custom_check],
        hypothesis_settings=hypothesis.settings(max_examples=1, database=None),
    )
    result = collect_result(engine)
    # Failures on different API operations
    assert len(result.failures) == 2
    failure = result.failures[0]
    assert failure.failure_info.failure.title == "Custom check failed: `custom_check`"
    if not exception_args:
        assert failure.failure_info.failure.message == ""
    else:
        assert failure.failure_info.failure.message == "Oops!"


def test_distinct_assertions(engine_factory):
    counter = 0

    # When a check contains different failing assertions
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

    engine = engine_factory(
        checks=[custom_check],
        hypothesis_settings=hypothesis.settings(max_examples=1, database=None),
    )
    result = collect_result(engine)
    # Then all of them should be reported
    assert len(result.failures) == 4
    assert {check.failure_info.failure.message for check in result.failures} == {
        "First",
        "Second",
        # Rewritten by pytest
        "assert None == 43\n +  where None = Case(body={'name': ''}).headers",
        "Fourth\nassert None == 43\n +  where None = Case(body={'name': ''}).headers",
    }


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"max_failures": 1}],
)
def test_flaky_assertions(engine_factory, kwargs):
    counter = 0

    # When a check contains different failing assertions and one of them is considered flaky by Hypothesis
    def custom_check(ctx, response, case):
        nonlocal counter
        counter += 1
        if counter == 1:
            raise AssertionError("First")
        if counter == 2:
            raise AssertionError("Second")
        assert case.headers == 43

    engine = engine_factory(
        checks=[custom_check],
        hypothesis_settings=hypothesis.settings(max_examples=1, database=None),
        **kwargs,
    )
    result = collect_result(engine)
    # Then all of them should be reported
    if "max_failures" in kwargs:
        assert len(result.failures) == 1
        assert {check.failure_info.failure.message for check in result.failures} == {"First"}
    else:
        # Assertions happen on multiple API operations (3 + 1)
        assert len(result.failures) == 4
        assert {check.failure_info.failure.message for check in result.failures} == {
            "First",
            "Second",
            # Rewritten by pytest
            "assert None == 43\n +  where None = Case(body={'name': ''}).headers",
            "assert None == 43\n +  where None = Case(path_parameters={'orderId': 42}).headers",
        }


def test_failure_hidden_behind_another_failure(engine_factory):
    # The same API operation, but one error is far less frequent and is located behind another one that happens more often, so it is not found in the first test suite

    suite_number = 0

    def dynamic_check(*args, **kwargs):
        if suite_number == 0:
            not_a_server_error(*args, **kwargs)
        else:
            response_schema_conformance(*args, **kwargs)

    engine = engine_factory(
        app_kwargs={"failure_behind_failure": True},
        checks=[dynamic_check],
        hypothesis_settings=hypothesis.settings(max_examples=60, database=None),
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
    engine = engine_factory(
        app_kwargs={"multiple_conformance_issues": True},
    )
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
        hypothesis_settings=hypothesis.settings(max_examples=60, database=None),
    )
    result = collect_result(engine)
    assert len(result.failures) == 1
    assert result.failures[0].failure_info.failure.title == "Use after free"
    assert result.events[-1].status == Status.FAILURE


def test_failed_health_check(engine_factory):
    def rejected_check(*args, **kwargs):
        hypothesis.reject()

    engine = engine_factory(
        hypothesis_settings=hypothesis.settings(max_examples=1, database=None, suppress_health_check=[]),
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
def test_flaky(engine_factory, kwargs):
    found = False

    def flaky_check(*args, **kwargs):
        nonlocal found
        if not found:
            found = True
            raise AssertionError("Flaky")

    engine = engine_factory(
        hypothesis_settings=hypothesis.settings(max_examples=1, database=None),
        checks=[flaky_check],
        **kwargs,
    )
    result = collect_result(engine)
    failures = result.failures
    assert len(failures) == 1
    assert failures[0].failure_info.failure.message == "Flaky"


def test_unsatisfiable(engine_factory):
    engine = engine_factory(
        app_kwargs={"unsatisfiable": True},
        hypothesis_settings=hypothesis.settings(max_examples=1, database=None),
    )
    result = collect_result(engine)
    assert result.errors
    assert isinstance(result.errors[0].value, hypothesis.errors.InvalidArgument)
    assert result.events[-1].status == Status.ERROR


def test_custom_headers(engine_factory):
    headers = {"X-Foo": "Bar"}
    engine = engine_factory(
        app_kwargs={"custom_headers": headers},
        hypothesis_settings=hypothesis.settings(max_examples=1, database=None),
        network=NetworkConfig(headers=headers),
    )
    result = collect_result(engine)
    assert result.events[-1].status == Status.SUCCESS


def test_multiple_source_links(engine_factory):
    # When there are multiple links coming to the same operation from different operations
    # Then there should be no error during getting the previous step results
    engine = engine_factory(
        app_kwargs={"multiple_source_links": True},
        hypothesis_settings=hypothesis.settings(max_examples=10, database=None),
    )
    result = collect_result(engine)
    assert not result.errors, result.errors


def test_max_response_time_valid(engine_factory):
    engine = engine_factory(
        hypothesis_settings=hypothesis.settings(max_examples=1, database=None),
        checks=[max_response_time],
        checks_config={max_response_time: MaxResponseTimeConfig(10.0)},
    )
    result = collect_result(engine)
    assert not result.errors, result.errors
    assert list(result.test_events[-2].recorder.checks.values())[0][0].name == "max_response_time"


def test_max_response_time_invalid(engine_factory):
    engine = engine_factory(
        app_kwargs={"slowdown": 0.010},
        hypothesis_settings=hypothesis.settings(max_examples=1, database=None, stateful_step_count=2),
        checks=[max_response_time],
        checks_config={max_response_time: MaxResponseTimeConfig(0.005)},
    )
    result = collect_result(engine)
    failures = result.failures
    # Failures on different API operations
    assert len(failures) == 2
    assert failures[0].failure_info.failure.message.startswith("Actual")
    assert failures[0].failure_info.failure.message.endswith("Limit: 5.00ms")


def test_targeted(engine_factory):
    calls = 0

    def custom_target(ctx):
        nonlocal calls
        calls += 1
        return 1.0

    engine = engine_factory(
        hypothesis_settings=hypothesis.settings(max_examples=1, database=None, stateful_step_count=5),
        checks=[not_a_server_error],
        targets=[custom_target],
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
    schema = schemathesis.openapi.from_dict(schema).configure(base_url=f"http://127.0.0.1:{root_app_port}/")
    engine = stateful.execute(
        engine=EngineContext(
            schema=schema,
            config=EngineConfig(
                execution=ExecutionConfig(
                    checks=CHECKS.get_all(),
                    targets=[],
                    hypothesis_settings=hypothesis.settings(max_examples=75, database=None),
                    generation=GenerationConfig(),
                ),
                network=NetworkConfig(),
                checks_config=ChecksConfig(),
            ),
            stop_event=threading.Event(),
        ),
        phase=Phase(name=PhaseName.STATEFUL_TESTING, is_supported=True, is_enabled=True),
    )
    result = collect_result(engine)
    assert result.events[-1].status == Status.FAILURE


def test_new_resource_is_not_available(engine_factory):
    # When a resource is not available after creation
    engine = engine_factory(
        app_kwargs={"ensure_resource_availability": True},
        hypothesis_settings=hypothesis.settings(max_examples=50, database=None),
    )
    result = collect_result(engine)
    # Then it is a failure
    assert result.events[-1].status == Status.FAILURE
    assert result.failures[0].failure_info.failure.title == "Resource is not available after creation"


def test_negative_tests(engine_factory):
    engine = engine_factory(
        app_kwargs={"independent_500": True},
        hypothesis_settings=hypothesis.settings(max_examples=50, database=None),
        configuration={"generation": GenerationConfig(modes=GenerationMode.all())},
    )
    result = collect_result(engine)
    assert result.events[-1].status == Status.FAILURE, result.errors


def test_unique_data(engine_factory):
    engine = engine_factory(
        app_kwargs={"independent_500": True},
        unique_data=True,
        hypothesis_settings=hypothesis.settings(max_examples=25, database=None, stateful_step_count=50),
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
        network=NetworkConfig(headers={"Authorization": f"Bearer {token}"}),
    )
    result = collect_result(engine)
    # Then no failures are reported
    assert result.events[-1].status == Status.SUCCESS


def test_ignored_auth_invalid(engine_factory):
    # When auth is ignored
    token = "Test"
    engine = engine_factory(
        app_kwargs={"auth_token": token, "ignored_auth": True},
        checks=[ignored_auth],
        network=NetworkConfig(headers={"Authorization": "Bearer UNKNOWN"}),
    )
    result = collect_result(engine)
    # Then it should be reported
    assert result.events[-1].status == Status.FAILURE
    assert result.failures[0].failure_info.failure.title == "Authentication declared but not enforced"
