from dataclasses import dataclass
import hypothesis
import hypothesis.errors
import pytest

import schemathesis
from schemathesis.checks import not_a_server_error
from schemathesis.specs.openapi.checks import response_schema_conformance, use_after_free
from schemathesis.stateful.runner import (
    events,
    StatefulTestRunnerConfig,
    _get_default_hypothesis_settings_kwargs,
    _get_hypothesis_settings_kwargs_override,
)


@pytest.fixture
def runner_factory(app_factory):
    def _runner_factory(app_kwargs=None, config_kwargs=None):
        app = app_factory(**(app_kwargs or {}))
        schema = schemathesis.from_wsgi("/openapi.json", app=app)
        state_machine = schema.as_state_machine()
        config_kwargs = config_kwargs or {}
        config_kwargs.setdefault("hypothesis_settings", hypothesis.settings(max_examples=10))
        return state_machine.runner(config=StatefulTestRunnerConfig(**config_kwargs))

    return _runner_factory


@dataclass
class RunnerResult:
    events: list[events.StatefulEvent]

    @property
    def event_names(self):
        return [event.__class__.__name__ for event in self.events]

    @property
    def failures(self):
        events_ = (event for event in self.events if isinstance(event, events.AfterSuite))
        return [failure for event in events_ for failure in event.failures]

    @property
    def errors(self):
        return [event for event in self.events if isinstance(event, events.Error)]


def collect_result(runner) -> RunnerResult:
    return RunnerResult(events=list(runner.execute()))


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
    # If `exit_first` is set
    if kwargs.get("exit_first"):
        # Then only the first one should be found
        assert len(result.failures) == 1
        assert result.failures[0].example.operation.verbose_name in all_affected_operations
    else:
        # Else, all of them should be found
        assert len(result.failures) == 2
        assert {check.example.operation.verbose_name for check in result.failures} == all_affected_operations


@pytest.mark.parametrize(
    "settings, expected",
    (
        (
            {},
            _get_default_hypothesis_settings_kwargs(),
        ),
        (
            {"phases": [hypothesis.Phase.explicit]},
            {"deadline": None},
        ),
        (_get_default_hypothesis_settings_kwargs(), {}),
    ),
)
def test_hypothesis_settings(settings, expected):
    assert _get_hypothesis_settings_kwargs_override(hypothesis.settings(**settings)) == expected


def test_create_runner_with_default_hypothesis_settings(runner_factory):
    runner = runner_factory(
        config_kwargs={"hypothesis_settings": hypothesis.settings(**_get_default_hypothesis_settings_kwargs())}
    )
    assert runner.config.hypothesis_settings.deadline is None


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
    assert "AfterStep" in result.event_names
    assert "BeforeStep" in result.event_names
    assert result.events[-1].status == events.RunStatus.INTERRUPTED


@pytest.mark.parametrize("event_cls", (events.BeforeScenario, events.AfterScenario))
def test_explicit_stop(runner_factory, event_cls):
    runner = runner_factory()
    collected = []
    for event in runner.execute():
        collected.append(event)
        if isinstance(event, event_cls):
            runner.stop()
    assert len(collected) > 0
    assert collected[-1].status == events.RunStatus.INTERRUPTED


def test_stop_outside_of_state_machine_execution(runner_factory, mocker):
    # When stop signal is received outside of state machine execution
    runner = runner_factory(app_kwargs={"independent_500": True})
    mocker.patch(
        "schemathesis.stateful.runner.FailureRegistry.mark_as_seen_in_run", side_effect=lambda *_, **__: runner.stop()
    )
    result = collect_result(runner)
    assert result.events[-2].status == events.SuiteStatus.INTERRUPTED
    assert result.events[-1].status == events.RunStatus.INTERRUPTED


def test_keyboard_interrupt(runner_factory, mocker):
    runner = runner_factory()
    mocker.patch.object(runner.event_queue, "get", side_effect=KeyboardInterrupt)
    result = collect_result(runner)
    assert events.Interrupted() in result.events


def test_internal_error_in_check(runner_factory):
    def bugged_check(*args, **kwargs):
        raise ZeroDivisionError("Oops!")

    runner = runner_factory(config_kwargs={"checks": (bugged_check,)})
    result = collect_result(runner)
    assert result.errors
    assert isinstance(result.errors[0].exception, ZeroDivisionError)


@pytest.mark.parametrize("exception_args", ((), ("Oops!",)))
def test_custom_assertion_in_check(runner_factory, exception_args):
    def custom_check(*args, **kwargs):
        raise AssertionError(*exception_args)

    runner = runner_factory(
        config_kwargs={"checks": (custom_check,), "hypothesis_settings": hypothesis.settings(max_examples=1)}
    )
    result = collect_result(runner)
    assert len(result.failures) == 1
    failure = result.failures[0]
    if not exception_args:
        assert failure.message == "Custom check failed: `custom_check`"
    else:
        assert failure.message == "Oops!"


def test_distinct_assertions(runner_factory):
    counter = 0

    # When a check contains different failing assertions
    def custom_check(*args, **kwargs):
        nonlocal counter
        counter += 1
        if counter == 1:
            raise AssertionError("First")
        else:
            raise AssertionError("Second")

    runner = runner_factory(
        config_kwargs={"checks": (custom_check,), "hypothesis_settings": hypothesis.settings(max_examples=1)}
    )
    result = collect_result(runner)
    # Then both of them should be reported
    assert len(result.failures) == 2
    assert {check.message for check in result.failures} == {"First", "Second"}


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
        config_kwargs={"hypothesis_settings": hypothesis.settings(max_examples=10), "checks": (dynamic_check,)},
    )
    failures = []
    for event in runner.execute():
        if isinstance(event, events.AfterSuite):
            failures.extend(event.failures)
            suite_number += 1
    assert len(failures) == 2
    assert {check.message for check in failures} == {"Response violates schema", "Server error"}


def test_multiple_conformance_issues(runner_factory):
    runner = runner_factory(app_kwargs={"multiple_conformance_issues": True})
    result = collect_result(runner)
    assert len(result.failures) == 2
    assert {check.message for check in result.failures} == {"Missing Content-Type header", "Response violates schema"}


def test_find_use_after_free(runner_factory):
    runner = runner_factory(
        app_kwargs={"use_after_free": True},
        config_kwargs={"checks": (use_after_free,), "hypothesis_settings": hypothesis.settings(max_examples=25)},
    )
    result = collect_result(runner)
    assert len(result.failures) == 1
    assert result.failures[0].message == "Use after free"
    assert result.events[-1].status == events.RunStatus.FAILURE


def test_failed_health_check(runner_factory):
    def rejected_check(*args, **kwargs):
        hypothesis.reject()

    runner = runner_factory(
        config_kwargs={"hypothesis_settings": hypothesis.settings(max_examples=10), "checks": (rejected_check,)},
    )
    result = collect_result(runner)
    assert result.errors
    assert isinstance(result.errors[0].exception, hypothesis.errors.FailedHealthCheck)
    assert result.events[-1].status == events.RunStatus.ERROR


def test_flaky(runner_factory):
    found = False

    def flaky_check(*args, **kwargs):
        nonlocal found
        if not found:
            found = True
            raise AssertionError("Flaky")

    runner = runner_factory(
        config_kwargs={"hypothesis_settings": hypothesis.settings(max_examples=10), "checks": (flaky_check,)},
    )
    failures = []
    for event in runner.execute():
        assert not isinstance(event, events.Error)
        if isinstance(event, events.AfterSuite):
            failures.extend(event.failures)
    assert len(failures) == 1
    assert failures[0].message == "Flaky"


def test_unsatisfiable(runner_factory):
    runner = runner_factory(app_kwargs={"unsatisfiable": True})
    result = collect_result(runner)
    assert result.errors
    assert isinstance(result.errors[0].exception, hypothesis.errors.InvalidArgument)
    assert result.events[-1].status == events.RunStatus.ERROR
