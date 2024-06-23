import json
from dataclasses import dataclass
from typing import List

import hypothesis
import hypothesis.errors
import pytest

from schemathesis.checks import not_a_server_error
from schemathesis.service.serialization import _serialize_stateful_event
from schemathesis.specs.openapi.checks import response_schema_conformance, use_after_free
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


@pytest.mark.parametrize(
    "kwargs",
    ({"exit_first": False}, {"exit_first": True}),
)
def test_find_independent_5xx(runner_factory, kwargs):
    # When the app contains multiple endpoints with 5xx responses
    runner = runner_factory(app_kwargs={"independent_500": True}, config_kwargs=kwargs)
    result = collect_result(runner)
    all_affected_operations = {
        "DELETE /users/{userId}",
        "PATCH /users/{userId}",
    }
    assert result.events[-1].status == events.RunStatus.FAILURE
    # There should be 2 or 1 final scenarios to reproduce failures depending on the `exit_first` setting
    scenarios = [
        event for event in result.events if isinstance(event, (events.ScenarioStarted, events.ScenarioFinished))
    ]
    num_of_final_scenarios = 1 if kwargs.get("exit_first") else 2
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
    if kwargs.get("exit_first"):
        # Then only the first one should be found
        assert len(result.failures) == 1
        assert result.failures[0].example.operation.verbose_name in all_affected_operations
    else:
        # Else, all of them should be found
        assert len(result.failures) == 2
        assert {check.example.operation.verbose_name for check in result.failures} == all_affected_operations
    serialize_all_events(result.events)


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
            "hypothesis_settings": hypothesis.settings(max_examples=20, database=None),
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
            "hypothesis_settings": hypothesis.settings(max_examples=1, database=None),
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
