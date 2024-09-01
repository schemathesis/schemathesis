import json
from dataclasses import dataclass
from typing import List

import hypothesis
import hypothesis.errors
import pytest

import schemathesis
from schemathesis.checks import not_a_server_error
from schemathesis.constants import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.extra._flask import run_server
from schemathesis.generation import DataGenerationMethod
from schemathesis.internal.copy import fast_deepcopy
from schemathesis.service.serialization import _serialize_stateful_event
from schemathesis.specs.openapi.checks import response_schema_conformance, use_after_free
from schemathesis.stateful.config import StatefulTestRunnerConfig
from schemathesis.stateful.runner import events
from schemathesis.stateful.sink import StateMachineSink
from test.utils import flaky


@dataclass
class RunnerResult:
    events: List[events.StatefulEvent]
    sink: StateMachineSink

    @property
    def event_names(self):
        return [event.__class__.__name__ for event in self.events]

    @property
    def failures(self):
        events_ = (event for event in self.events if isinstance(event, events.SuiteFinished))
        return [failure for event in events_ for failure in event.failures]

    @property
    def errors(self):
        return [event for event in self.events if isinstance(event, events.Errored)]

    @property
    def steps_before_first_failure(self):
        steps = 0
        for event in self.events:
            if isinstance(event, events.StepFinished):
                if event.status == events.RunStatus.FAILURE:
                    break
                steps += 1
        return steps

    @property
    def responses(self):
        return [event.response for event in self.events if isinstance(event, events.StepFinished)]


def serialize_all_events(events):
    for event in events:
        json.dumps(_serialize_stateful_event(event))


def collect_result(runner) -> RunnerResult:
    sink = runner.state_machine.sink()
    events_ = []
    for event in runner.execute():
        sink.consume(event)
        events_.append(event)
    return RunnerResult(events=events_, sink=sink)


def assert_linked_calls_followed(result: RunnerResult):
    # Every successful POST should have a linked call followed
    steps = [event for event in result.events if isinstance(event, events.StepFinished)]
    ids = {
        "POST": set(),
        "GET": set(),
        "DELETE": set(),
        "PATCH": set(),
    }
    sources = set()
    for event in steps:
        ids[event.response.request.method].add(event.response.request.headers[SCHEMATHESIS_TEST_CASE_HEADER])
        if event.response.request.method != "POST":
            if event.case.source.response.request.method == "POST":
                sources.add(event.case.source.response.request.headers[SCHEMATHESIS_TEST_CASE_HEADER])
    # Most POSTs should be followed by a GET, DELETE, or PATCH
    assert len(sources) - len(ids["POST"]) < 10


@pytest.mark.parametrize(
    "kwargs",
    (
        {"exit_first": False},
        {"exit_first": True},
        {"max_failures": 1},
        {"max_failures": 2},
    ),
)
def test_find_independent_5xx(runner_factory, kwargs):
    # When the app contains multiple endpoints with 5xx responses
    runner = runner_factory(app_kwargs={"independent_500": True}, config_kwargs=kwargs)
    result = collect_result(runner)
    all_affected_operations = {
        "DELETE /users/{userId}",
        "PATCH /users/{userId}",
    }
    assert result.events[-1].status == events.RunStatus.FAILURE, result.errors
    # There should be 2 or 1 final scenarios to reproduce failures depending on the `exit_first` setting
    scenarios = [
        event for event in result.events if isinstance(event, (events.ScenarioStarted, events.ScenarioFinished))
    ]
    num_of_final_scenarios = 1 if kwargs.get("exit_first") or kwargs.get("max_failures") == 1 else 2
    assert len([s for s in scenarios if s.is_final and isinstance(s, events.ScenarioStarted)]) == num_of_final_scenarios
    assert (
        len(
            [
                s
                for s in scenarios
                if s.is_final and isinstance(s, events.ScenarioFinished) and s.status == events.ScenarioStatus.FAILURE
            ]
        )
        == num_of_final_scenarios
    )
    for event in result.events:
        assert event.timestamp is not None
    # If `exit_first` is set
    if kwargs.get("exit_first") or kwargs.get("max_failures") == 1:
        # Then only the first one should be found
        assert len(result.failures) == 1
        assert result.failures[0].example.operation.verbose_name in all_affected_operations
    elif kwargs.get("exit_first") is False or kwargs.get("max_failures") == 2:
        # Else, all of them should be found
        assert len(result.failures) == 2
        assert {check.example.operation.verbose_name for check in result.failures} == all_affected_operations
    serialize_all_events(result.events)
    assert_linked_calls_followed(result)


def test_works_on_single_link(runner_factory):
    runner = runner_factory(app_kwargs={"single_link": True, "independent_500": True})
    result = collect_result(runner)
    assert result.events[-1].status == events.RunStatus.FAILURE, result.errors


def keyboard_interrupt(r):
    raise KeyboardInterrupt


def stop_runner(r):
    r.stop()


@pytest.mark.parametrize("func", (keyboard_interrupt, stop_runner))
def test_stop_in_check(runner_factory, func):
    def stop_immediately(*args, **kwargs):
        func(runner)

    runner = runner_factory(config_kwargs={"checks": (stop_immediately,)})
    result = collect_result(runner)
    assert result.sink.duration and result.sink.duration > 0
    assert result.sink.suites[events.SuiteStatus.INTERRUPTED] == 1
    assert "StepStarted" in result.event_names
    assert "StepFinished" in result.event_names
    assert result.events[-1].status == events.RunStatus.INTERRUPTED
    serialize_all_events(result.events)


@pytest.mark.parametrize("event_cls", (events.ScenarioStarted, events.ScenarioFinished))
def test_explicit_stop(runner_factory, event_cls):
    runner = runner_factory()
    sink = runner.state_machine.sink()
    assert sink.duration is None
    collected = []
    for event in runner.execute():
        collected.append(event)
        if isinstance(event, event_cls):
            runner.stop()
    assert len(collected) > 0
    assert collected[-1].status == events.RunStatus.INTERRUPTED


def test_stop_outside_of_state_machine_execution(runner_factory, mocker):
    # When stop signal is received outside of state machine execution
    runner = runner_factory(
        app_kwargs={"independent_500": True},
    )
    mocker.patch(
        "schemathesis.stateful.runner.RunnerContext.mark_as_seen_in_run", side_effect=lambda *_, **__: runner.stop()
    )
    result = collect_result(runner)
    assert result.events[-2].status == events.SuiteStatus.INTERRUPTED
    assert result.events[-1].status == events.RunStatus.INTERRUPTED
    serialize_all_events(result.events)


def test_keyboard_interrupt(runner_factory, mocker):
    runner = runner_factory()
    mocker.patch.object(runner.event_queue, "get", side_effect=KeyboardInterrupt)
    result = collect_result(runner)
    assert "Interrupted" in result.event_names


def test_internal_error_in_check(runner_factory):
    def bugged_check(*args, **kwargs):
        raise ZeroDivisionError("Oops!")

    runner = runner_factory(config_kwargs={"checks": (bugged_check,)})
    result = collect_result(runner)
    assert result.errors
    assert isinstance(result.errors[0].exception, ZeroDivisionError)
    serialize_all_events(result.events)


@pytest.mark.parametrize("exception_args", ((), ("Oops!",)))
def test_custom_assertion_in_check(runner_factory, exception_args):
    def custom_check(*args, **kwargs):
        raise AssertionError(*exception_args)

    runner = runner_factory(
        config_kwargs={
            "checks": (custom_check,),
            "hypothesis_settings": hypothesis.settings(max_examples=1, database=None),
        }
    )
    result = collect_result(runner)
    assert len(result.failures) == 1
    failure = result.failures[0]
    if not exception_args:
        assert failure.message == "Custom check failed: `custom_check`"
    else:
        assert failure.message == "Oops!"
    serialize_all_events(result.events)


def test_distinct_assertions(runner_factory):
    counter = 0

    # When a check contains different failing assertions
    def custom_check(response, case):
        nonlocal counter
        counter += 1
        if counter == 1:
            raise AssertionError("First")
        elif counter == 2:
            raise AssertionError("Second")
        elif counter == 3:
            # No message
            assert case.headers == 43
        elif counter == 4:
            # With message
            assert case.headers == 43, "Fourth"

    runner = runner_factory(
        config_kwargs={
            "checks": (custom_check,),
            "hypothesis_settings": hypothesis.settings(max_examples=1, database=None),
        }
    )
    result = collect_result(runner)
    # Then all of them should be reported
    assert len(result.failures) == 4
    assert {check.message for check in result.failures} == {
        "First",
        "Second",
        # Rewritten by pytest
        "assert None == 43\n +  where None = Case(body={'name': ''}).headers",
        "Fourth\nassert {} == 43\n +  where {} = Case(headers={}, body={'name': ''}).headers",
    }


@pytest.mark.parametrize(
    "kwargs",
    ({"exit_first": False}, {"exit_first": True}),
)
def test_flaky_assertions(runner_factory, kwargs):
    counter = 0

    # When a check contains different failing assertions and one of them is considered flaky by Hypothesis
    def custom_check(response, case):
        nonlocal counter
        counter += 1
        if counter == 1:
            raise AssertionError("First")
        elif counter == 2:
            raise AssertionError("Second")
        else:
            assert case.headers == 43

    runner = runner_factory(
        config_kwargs={
            "checks": (custom_check,),
            "hypothesis_settings": hypothesis.settings(max_examples=1, database=None),
            **kwargs,
        }
    )
    result = collect_result(runner)
    # Then all of them should be reported
    if kwargs.get("exit_first"):
        assert len(result.failures) == 2
        assert {check.message for check in result.failures} == {"First", "Second"}
    else:
        assert len(result.failures) == 3
        assert {check.message for check in result.failures} == {
            "First",
            "Second",
            # Rewritten by pytest
            "assert None == 43\n +  where None = Case(body={'name': ''}).headers",
        }


def test_failure_hidden_behind_another_failure(runner_factory):
    # The same API operation, but one error is far less frequent and is located behind another one that happens more often, so it is not found in the first test suite

    suite_number = 0

    def dynamic_check(*args, **kwargs):
        if suite_number == 0:
            not_a_server_error(*args, **kwargs)
        else:
            response_schema_conformance(*args, **kwargs)

    runner = runner_factory(
        app_kwargs={"failure_behind_failure": True},
        config_kwargs={"checks": (dynamic_check,)},
    )
    failures = []
    for event in runner.execute():
        if isinstance(event, events.SuiteFinished):
            failures.extend(event.failures)
            suite_number += 1
    assert len(failures) == 2
    assert {check.message for check in failures} == {"Response violates schema", "Server error"}


def test_multiple_conformance_issues(runner_factory):
    runner = runner_factory(
        app_kwargs={"multiple_conformance_issues": True},
    )
    result = collect_result(runner)
    assert len(result.failures) == 2
    assert {check.message for check in result.failures} == {"Missing Content-Type header", "Response violates schema"}


@flaky(max_runs=10, min_passes=1)
def test_find_use_after_free(runner_factory):
    runner = runner_factory(
        app_kwargs={"use_after_free": True},
        config_kwargs={
            "checks": (use_after_free,),
            "hypothesis_settings": hypothesis.settings(max_examples=60, database=None),
        },
    )
    result = collect_result(runner)
    assert len(result.sink.transitions.roots["POST /users"]) > 0
    assert result.sink.suites[events.SuiteStatus.FAILURE] == 1
    assert result.sink.suites[events.SuiteStatus.SUCCESS] == 1
    assert len(result.failures) == 1
    assert result.failures[0].message == "Use after free"
    assert result.events[-1].status == events.RunStatus.FAILURE
    assert result.sink.transitions.to_formatted_table(80).splitlines()[:3] == [
        "Links                                                 2xx    4xx    5xx    Total",
        "",
        "DELETE /orders/{orderId}",
    ]


def test_failed_health_check(runner_factory):
    def rejected_check(*args, **kwargs):
        hypothesis.reject()

    runner = runner_factory(
        config_kwargs={
            "hypothesis_settings": hypothesis.settings(max_examples=1, database=None, suppress_health_check=[]),
            "checks": (rejected_check,),
        },
    )
    result = collect_result(runner)
    assert result.errors
    assert isinstance(result.errors[0].exception, hypothesis.errors.FailedHealthCheck)
    assert result.events[-1].status == events.RunStatus.ERROR
    serialize_all_events(result.events)


@pytest.mark.parametrize(
    "kwargs",
    ({"exit_first": False}, {"exit_first": True}),
)
def test_flaky(runner_factory, kwargs):
    found = False

    def flaky_check(*args, **kwargs):
        nonlocal found
        if not found:
            found = True
            raise AssertionError("Flaky")

    runner = runner_factory(
        config_kwargs={
            "hypothesis_settings": hypothesis.settings(max_examples=1, database=None),
            "checks": (flaky_check,),
            **kwargs,
        }
    )
    failures = []
    for event in runner.execute():
        assert not isinstance(event, events.Errored)
        if isinstance(event, events.SuiteFinished):
            failures.extend(event.failures)
    assert len(failures) == 1
    assert failures[0].message == "Flaky"


def test_unsatisfiable(runner_factory):
    runner = runner_factory(
        app_kwargs={"unsatisfiable": True},
        config_kwargs={"hypothesis_settings": hypothesis.settings(max_examples=1, database=None)},
    )
    result = collect_result(runner)
    assert result.errors
    assert isinstance(result.errors[0].exception, hypothesis.errors.InvalidArgument)
    assert result.events[-1].status == events.RunStatus.ERROR


def test_random_unsatisfiable(runner_factory):
    runner = runner_factory(
        config_kwargs={"hypothesis_settings": hypothesis.settings(max_examples=25, database=None)},
    )

    @runner.state_machine.schema.hook
    def map_body(ctx, body):
        if len(body["name"]) % 3 == 2:
            raise hypothesis.errors.Unsatisfiable("Occurs randomly")
        return body

    result = collect_result(runner)
    assert not result.errors, result.errors


def test_custom_headers(runner_factory):
    headers = {"X-Foo": "Bar"}
    runner = runner_factory(
        app_kwargs={"custom_headers": headers},
        config_kwargs={"hypothesis_settings": hypothesis.settings(max_examples=1, database=None), "headers": headers},
    )
    result = collect_result(runner)
    assert result.events[-1].status == events.RunStatus.SUCCESS


def test_multiple_source_links(runner_factory):
    # When there are multiple links coming to the same operation from different operations
    # Then there should be no error during getting the previous step results
    runner = runner_factory(
        app_kwargs={"multiple_source_links": True},
        config_kwargs={"hypothesis_settings": hypothesis.settings(max_examples=10, database=None)},
    )
    result = collect_result(runner)
    assert not result.errors, result.errors


def test_dry_run(runner_factory):
    runner = runner_factory(
        config_kwargs={
            "hypothesis_settings": hypothesis.settings(max_examples=1, database=None),
            "dry_run": True,
        },
    )
    result = collect_result(runner)
    assert not result.errors, result.errors
    for event in result.events:
        if isinstance(event, events.StepFinished):
            assert event.response is None
            assert not event.checks


def test_max_response_time_valid(runner_factory):
    runner = runner_factory(
        config_kwargs={
            "hypothesis_settings": hypothesis.settings(max_examples=1, database=None),
            "max_response_time": 10000,
        },
    )
    result = collect_result(runner)
    assert not result.errors, result.errors
    assert result.events[-4].checks[-1].name == "max_response_time"


def test_max_response_time_invalid(runner_factory):
    runner = runner_factory(
        app_kwargs={"slowdown": 0.010},
        config_kwargs={
            "hypothesis_settings": hypothesis.settings(max_examples=1, database=None, stateful_step_count=2),
            "max_response_time": 5,
        },
    )
    failures = []
    for event in runner.execute():
        assert not isinstance(event, events.Errored)
        if isinstance(event, events.SuiteFinished):
            failures.extend(event.failures)
    assert len(failures) == 1
    assert failures[0].message.startswith("Actual")
    assert failures[0].message.endswith("Limit: 5.00ms")


def test_targeted(runner_factory):
    calls = 0

    def custom_target(ctx):
        nonlocal calls
        calls += 1
        return 1.0

    runner = runner_factory(
        config_kwargs={
            "hypothesis_settings": hypothesis.settings(max_examples=1, database=None, stateful_step_count=5),
            "checks": (not_a_server_error,),
            "targets": [custom_target],
        },
    )
    result = collect_result(runner)
    assert not result.errors, result.errors
    assert calls > 0


def test_external_link(empty_open_api_3_schema, app_factory):
    empty_open_api_3_schema = fast_deepcopy(empty_open_api_3_schema)
    remote_app = app_factory(independent_500=True)
    remote_app_port = run_server(remote_app)
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
    empty_open_api_3_schema["paths"] = {
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
    }
    empty_open_api_3_schema["components"] = {
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
    }
    root_app = app_factory(independent_500=True)
    schema = schemathesis.from_dict(empty_open_api_3_schema, app=root_app)
    state_machine = schema.as_state_machine()
    runner = state_machine.runner(
        config=StatefulTestRunnerConfig(hypothesis_settings=hypothesis.settings(max_examples=75, database=None))
    )
    result = collect_result(runner)
    assert result.events[-1].status == events.RunStatus.FAILURE


def test_new_resource_is_not_available(runner_factory):
    # When a resource is not available after creation
    runner = runner_factory(
        app_kwargs={"ensure_resource_availability": True},
        config_kwargs={
            "hypothesis_settings": hypothesis.settings(max_examples=50, database=None),
        },
    )
    result = collect_result(runner)
    # Then it is a failure
    assert result.events[-1].status == events.RunStatus.FAILURE
    assert result.failures[0].message == "Resource is not available after creation"


def test_negative_tests(runner_factory):
    runner = runner_factory(
        app_kwargs={"independent_500": True},
        config_kwargs={
            "hypothesis_settings": hypothesis.settings(max_examples=50, database=None),
        },
        loader_kwargs={"data_generation_methods": DataGenerationMethod.all()},
    )
    result = collect_result(runner)
    assert result.events[-1].status == events.RunStatus.FAILURE, result.errors
