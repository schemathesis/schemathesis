import click
import hypothesis
import pytest

import schemathesis
import schemathesis.cli.context
from schemathesis import models, runner
from schemathesis.cli.output import default
from schemathesis.cli.output.default import display_internal_error
from schemathesis.core import NOT_SET
from schemathesis.models import Case, OperationDefinition
from schemathesis.runner.events import Finished, InternalError
from schemathesis.runner.models import Check, Request, Response, Status, TestResult, TestResultSet

from ...utils import strip_style_win32


@pytest.fixture(autouse=True)
def click_context():
    """Add terminal colors to the output in tests."""
    with click.Context(schemathesis.cli.run, color=True):
        yield


@pytest.fixture
def execution_context():
    return schemathesis.cli.context.ExecutionContext(hypothesis.settings(), [], operations_count=1)


@pytest.fixture
def operation(swagger_20):
    return models.APIOperation(
        "/success",
        "GET",
        definition=OperationDefinition({}, {}, ""),
        base_url="http://127.0.0.1:8080",
        schema=swagger_20,
    )


@pytest.fixture
def response():
    body = b'{"id": 5}'
    return Response(
        status_code=201,
        body=body,
        body_size=len(body),
        message="Created",
        encoding="utf-8",
        http_version="1.1",
        elapsed=1.0,
        headers={"Content-Type": ["application/json"]},
        verify=True,
    )


@pytest.fixture
def results_set(operation):
    statistic = TestResult(verbose_name=f"{operation.method} {operation.full_path}")
    return TestResultSet(seed=42, results=[statistic])


@pytest.fixture
def after_execution(results_set):
    return runner.events.AfterExecution.from_result(
        result=results_set.results[0], status=Status.success, elapsed_time=1.0, correlation_id="foo"
    )


def test_get_terminal_width():
    assert default.get_terminal_width() >= 80


@pytest.mark.parametrize(
    ("title", "separator", "expected"),
    [
        ("TEST", "-", "------- TEST -------"),
        ("TEST", "*", "******* TEST *******"),
    ],
)
def test_display_section_name(capsys, title, separator, expected):
    # When section name is displayed
    default.display_section_name(title, separator=separator)
    out = capsys.readouterr().out.strip()
    terminal_width = default.get_terminal_width()
    # It should fit into the terminal width
    assert len(click.unstyle(out)) == terminal_width
    # And the section name should be bold
    assert strip_style_win32(click.style(click.unstyle(out), bold=True)) == out
    assert expected in out


def test_handle_initialized(capsys, mocker, execution_context, swagger_20):
    # Given Initialized event
    event = runner.events.Initialized.from_schema(schema=swagger_20, seed=42)
    # When this even is handled
    default.handle_initialized(execution_context, event)
    default.handle_before_probing(execution_context, mocker.Mock(auto_spec=True))
    default.handle_after_probing(execution_context, mocker.Mock(probes=None))
    out = capsys.readouterr().out
    lines = out.split("\n")
    # Then initial title is displayed
    assert " Schemathesis test session starts " in lines[0]
    # And number of collected operations
    assert strip_style_win32(click.style("Collected API operations: 1", bold=True)) in lines
    # And the output has an empty line in the end
    assert out.endswith("\n")


def test_display_statistic(capsys, execution_context, operation, response):
    # Given multiple successful & failed checks in a single test
    success = Check("not_a_server_error", Status.success, response, 0, Case(operation, generation_time=0.0))
    failure = Check("not_a_server_error", Status.failure, response, 0, Case(operation, generation_time=0.0))
    single_test_statistic = TestResult(
        verbose_name=f"{operation.method} {operation.full_path}",
        checks=[
            success,
            success,
            success,
            failure,
            failure,
            Check("different_check", Status.success, response, 0, Case(operation, generation_time=0.0)),
        ],
    )
    results = TestResultSet(seed=42, results=[single_test_statistic])
    event = Finished.from_results(results, running_time=1.0)
    # When test results are displayed
    default.display_statistic(execution_context, event)

    lines = [line for line in capsys.readouterr().out.split("\n") if line]
    failed = strip_style_win32(click.style("FAILED", bold=True, fg="red"))
    passed = strip_style_win32(click.style("PASSED", bold=True, fg="green"))
    # Then all check results should be properly displayed with relevant colors
    assert lines[2:4] == [
        f"    not_a_server_error                    3 / 5 passed          {failed} ",
        f"    different_check                       1 / 1 passed          {passed} ",
    ]


def test_display_multiple_warnings(capsys, execution_context):
    results = TestResultSet(seed=42, results=[])
    results.add_warning("Foo")
    results.add_warning("Bar")
    event = Finished.from_results(results, running_time=1.0)
    # When test results are displayed
    default.display_statistic(execution_context, event)
    lines = [click.unstyle(line) for line in capsys.readouterr().out.split("\n") if line]
    assert lines[2:5] == [
        "WARNINGS:",
        "  - Foo",
        "  - Bar",
    ]


def test_display_statistic_empty(capsys, execution_context, results_set):
    default.display_statistic(execution_context, Finished.from_results(results_set, running_time=1.23))
    assert capsys.readouterr().out.split("\n")[2] == strip_style_win32(
        click.style("No checks were performed.", bold=True)
    )


def test_display_statistic_junitxml(capsys, execution_context, results_set):
    xml_path = "/tmp/junit.xml"
    execution_context.junit_xml_file = xml_path
    default.display_statistic(execution_context, Finished.from_results(results_set, running_time=1.23))
    assert capsys.readouterr().out.split("\n")[4] == strip_style_win32(
        click.style("JUnit XML file", bold=True) + click.style(f": {xml_path}")
    )


@pytest.mark.parametrize(
    ("position", "length", "expected"), [(1, 100, "[  1%]"), (20, 100, "[ 20%]"), (100, 100, "[100%]")]
)
def test_get_percentage(position, length, expected):
    assert default.get_percentage(position, length) == expected


@pytest.mark.parametrize("current_line_length", [0, 20])
@pytest.mark.parametrize(("operations_processed", "percentage"), [(0, "[  0%]"), (1, "[100%]")])
def test_display_percentage(
    capsys, execution_context, after_execution, current_line_length, operations_processed, percentage
):
    execution_context.current_line_length = current_line_length
    execution_context.operations_processed = operations_processed
    # When percentage is displayed
    default.display_percentage(execution_context, after_execution)
    out = capsys.readouterr().out
    # Then the whole line fits precisely to the terminal width. Note `-1` is padding, that is calculated in a
    # different place when the line is printed
    assert len(click.unstyle(out)) + current_line_length - 1 == default.get_terminal_width()
    # And the percentage displayed as expected in cyan color
    assert out.strip() == strip_style_win32(click.style(percentage, fg="cyan"))


@pytest.mark.parametrize("body", [{}, {"foo": "bar"}, NOT_SET])
def test_display_single_failure(capsys, execution_context, operation, body, response):
    # Given a single test result with multiple successful & failed checks
    media_type = "application/json" if body is not NOT_SET else None
    success = Check(
        "not_a_server_error",
        Status.success,
        Request(method="POST", uri="http://user:pass@127.0.0.1/path", body=None, body_size=None, headers={}),
        response,
        Case(operation, generation_time=0.0, body=body, media_type=media_type),
    )
    failure = Check(
        "not_a_server_error",
        Status.failure,
        Request(method="POST", uri="http://user:pass@127.0.0.1/path", body=None, body_size=None, headers={}),
        response,
        Case(operation, generation_time=0.0, body=body, media_type=media_type),
    )
    test_statistic = TestResult(
        verbose_name=f"{operation.method} {operation.full_path}",
        checks=[
            success,
            success,
            success,
            failure,
            failure,
            Check(
                "different_check",
                Status.success,
                Request(method="POST", uri="http://user:pass@127.0.0.1/path", body=None, body_size=None, headers={}),
                response,
                Case(
                    operation,
                    generation_time=0.0,
                    body=body,
                    media_type=media_type,
                ),
            ),
        ],
    )
    # When this failure is displayed
    default.display_failures_for_single_test(execution_context, test_statistic)
    out = capsys.readouterr().out
    lines = out.split("\n")
    # Then the path is displayed as a subsection
    assert " GET /v1/success " in lines[0]


@pytest.mark.parametrize(
    ("status", "expected_symbol", "color"),
    [(Status.success, ".", "green"), (Status.failure, "F", "red"), (Status.error, "E", "red")],
)
def test_handle_after_execution(capsys, execution_context, after_execution, status, expected_symbol, color):
    # Given AfterExecution even with certain status
    after_execution.status = status
    # When this event is handled
    default.handle_after_execution(execution_context, after_execution)

    assert after_execution.result.verbose_name == "GET /v1/success"

    lines = capsys.readouterr().out.strip().split("\n")
    symbol, percentage = lines[0].split()
    # Then the symbol corresponding to the status is displayed with a proper color
    assert strip_style_win32(click.style(expected_symbol, fg=color)) == symbol
    # And percentage is displayed in cyan color
    assert strip_style_win32(click.style("[100%]", fg="cyan")) == percentage


def test_after_execution_attributes(execution_context, after_execution):
    # When `handle_after_execution` is executed
    default.handle_after_execution(execution_context, after_execution)
    # Then number of operations processed grows by 1
    assert execution_context.operations_processed == 1
    # And the line length grows by 1 symbol
    assert execution_context.current_line_length == 1

    default.handle_after_execution(execution_context, after_execution)
    assert execution_context.operations_processed == 2
    assert execution_context.current_line_length == 2


@pytest.mark.parametrize("show_trace", [True, False])
def test_display_internal_error(capsys, execution_context, show_trace):
    execution_context.show_trace = show_trace
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError as exc:
        event = InternalError.from_exc(exc)
        display_internal_error(execution_context, event)
        out = capsys.readouterr().out.strip()
        assert ("Traceback (most recent call last):" in out) is show_trace
        assert "ZeroDivisionError: division by zero" in out


def test_display_summary(capsys, results_set):
    # Given the Finished event
    event = runner.events.Finished.from_results(results=results_set, running_time=1.257)
    # When `display_summary` is called
    default.display_summary(event)
    out = capsys.readouterr().out.strip()
    # Then number of total tests & total running time should be displayed
    assert "=== 1 passed in 1.26s ===" in out
    # And it should be in green & bold style
    assert strip_style_win32(click.style(click.unstyle(out), fg="green", bold=True)) == out
